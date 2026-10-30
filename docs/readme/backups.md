# PostgreSQL Backups and Point-in-Time Recovery

pgBackRest ships every WAL segment and scheduled backups of the PostgreSQL cluster to an
S3 bucket (Hetzner Object Storage), encrypted. Any moment inside the retention window can
be restored. The logical dump (`make backup`) stays as the portable copy; see
*Which tool when* below.

Backups are off by default. `BACKUP_ENABLED=false` makes the archive command a no-op and
starts no sidecar.

## What is backed up

| Item | Value |
| --- | --- |
| Data loss on a lost box (RPO) | about a minute while the bucket is reachable: `archive_timeout = 60` closes a quiet WAL segment every minute and the async archiver uploads it |
| Full backup | Sundays 22:00 UTC (03:00 Asia/Tashkent) |
| Differential backup | Monday to Saturday 22:00 UTC |
| Archive check | every hour at minute 17 |
| Retention | 14 days (`repo1-retention-full-type=time`, `repo1-retention-full=14`): every full backup needed to restore a moment of the last 14 days stays, with its differentials and WAL |
| Encryption | `aes-256-cbc`, passphrase `PGBACKREST_REPO1_CIPHER_PASS` |
| Bucket | Object Lock COMPLIANCE, default retention 9 days; lifecycle expires noncurrent versions after 10 days |
| Stanza | `main`, path `/pgbackrest` in the bucket |

The schedule is `infra/backup/crontab`, run by supercronic in the `backup` sidecar. The
sidecar runs the postgres image on the same data volume and the same unix socket
(`postgres-socket` volume), because pgBackRest reads the data directory.

Object Lock is the protection against a stolen key: a COMPLIANCE lock cannot be removed by
anyone, the account owner included, so while a version is locked a delete request only adds
a delete marker and the version stays readable and restorable. The lock lasts 9 days, longer
than the weekly full-backup interval, so the newest full backup and everything after it are
always locked; an older version whose lock has ended can be deleted for good by whoever holds
the key. Lifecycle removes noncurrent versions 10 days after they become noncurrent, which is
why the bucket does not grow without bound, and also the window for undoing a stolen key's
delete markers (*A stolen bucket key* below).

The passphrase is the one thing the bucket cannot give back. Keep a copy outside the server
(a password manager); without it every backup in the bucket is unreadable.

## Enabling backups on a box

1. Create the bucket in the Hetzner console with **Object Lock enabled**. Hetzner allows the
   lock only at creation, and versioning comes with it. S3 credentials reach every bucket of
   their Hetzner project: in a project of its own the backup key is isolated, in a shared one
   (next to the media bucket) the other keys can read the backups and add delete markers. The
   lock and the encryption hold either way, so a shared project is a valid choice.
2. Create an access key for that project.
3. Apply the lock default and the lifecycle rules, with the keys in the environment so they
   never touch a file:

   ```bash
   AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... make backup-bucket BUCKET=<bucket> REGION=<fsn1|nbg1|hel1>
   AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... scripts/ops/backup_bucket.sh show <bucket> <region>
   ```

   `show` prints versioning, the lock and the lifecycle; check all three.
4. Create two Fettle heartbeats (or any monitor that takes a URL ping):
   - backup: `period` 86400 s, `grace` 21600 s. A scheduled run pings `/start`, then the
     bare URL on success or `/fail` on failure.
   - archive: `period` 3600 s, `grace` 1800 s. The hourly check pings the bare URL when
     archiving works and `/fail` otherwise.
5. On the box, create `.env.backup` from the example, owned by `deploy` like `.env`:
   `sudo install -o deploy -g deploy -m 0600 .env.backup.example .env.backup`, and fill it:
   bucket, endpoint (no scheme, e.g. `nbg1.your-objectstorage.com`), region, the key pair,
   `PGBACKREST_REPO1_CIPHER_PASS` (`openssl rand -hex 32`) and the two heartbeat URLs. Values
   are written bare: no quotes, `$` or `#`; `check_env.py` refuses them. Compose loads this
   file into the postgres and backup containers only.
6. Save the passphrase and the box's `.env` in the password manager.
7. In `.env` set `BACKUP_ENABLED=true`. `BACKUP_ARCHIVE_QUEUE_MAX` (default `4GiB`) is the
   WAL the box holds while the bucket is unreachable: a whole number with a unit (`MiB`,
   `GiB`, `TiB`), at least `16MiB`. `check_env.py` refuses any other spelling, because
   pgBackRest would reject it at every archive call and nothing would bound the queue.
8. Deploy. `deploy.sh` runs `stanza-create` and `pgbackrest check`, then starts the sidecar,
   and refuses to continue when either fails. Take the first full backup right away rather
   than waiting for Sunday: `make backup-now type=full`.

`BACKUP_ENABLED` and every value in `.env.backup` are part of the postgres container's
environment, so changing either (steps 5 and 7 here, a rotated key, turning backups off) makes
the next deploy recreate the postgres container: the database restarts and the app sees a few
seconds of refused connections. Deploy such a change at a quiet hour.

`make backup-info` lists the backups and the WAL range in the repository.

A manual `make backup-now` does not ping the heartbeat, and a failure there pages nobody;
only the scheduled runs report.

Do not deploy at 22:00 UTC. A deploy that recreates the sidecar kills the backup running
at that moment, and the heartbeat goes red.

## Alerts

| Heartbeat | Red means | Do |
| --- | --- | --- |
| archive | `pgbackrest check` failed: WAL is not reaching the bucket, or a segment was dropped from the queue and a gap is open | `make logs s=backup` and `make logs s=postgres`; test the bucket and keys with `make backup-info`; fix the cause, then `make backup-now type=diff` if the gap is still open |
| backup | the last scheduled backup failed or did not start | `make logs s=backup`; fix the cause, run `make backup-now type=full` |

The archive heartbeat also goes red when the async queue hit `archive-push-queue-max`
(`BACKUP_ARCHIVE_QUEUE_MAX`). pgBackRest then drops WAL instead of filling the disk, which
leaves a hole recovery cannot cross. The hourly check notices the drop in the pgBackRest
log, pings archive `/fail` and starts a differential backup that closes the gap, and
retries it every hour until one succeeds. When a scheduled backup is running at that moment,
the differential steps aside without paging the backup heartbeat, and the running backup
closes the gap when it succeeds. The heartbeat turns green again after a backup succeeds.
Until then a restore reaches only the moment before the hole.

The causes seen most often: the bucket or endpoint is unreachable, a key was revoked, the
disk is full, or `.env.backup` lost a value after an edit.

## Monthly verification drill

```bash
make backup-verify
make backup-verify target="2026-10-30 14:03:00+00"
```

The drill restores the newest backup (or the given moment) into a throwaway volume, starts
PostgreSQL in a throwaway container, prints the last replayed transaction and the largest
tables with their row counts, and removes the container and volume. It never touches the
live data volume and never archives. `VERIFY_MEMORY` and `VERIFY_CPUS` (default `1g` and `1`)
cap the container so the drill cannot starve the live server. Ctrl-C stops it and removes
both. Run it on the live box once a month and after any change to the backup settings.

Read the table counts against what you know the database holds. Then judge the age check:
without `target` the drill fails when the last replayed transaction is older than two
hours (`VERIFY_MAX_REPLAY_AGE_SECONDS` overrides the limit, in seconds). The check applies
only without a target, since a drill to a past moment is old by design.

A failure on age has two causes:

- A quiet database. No write for two hours leaves nothing newer to replay. Look at whether
  the database had writes in the window (the app's last activity, `pg_stat_database` counters
  on the live server). If it was idle, rerun with a larger limit:
  `VERIFY_MAX_REPLAY_AGE_SECONDS=86400 make backup-verify`.
- A real WAL gap or archiving that stopped. Writes happened, but the repository does not
  hold them. Check the archive heartbeat and `make backup-info` (the WAL range should end a
  minute or two ago), find why archiving stopped, and take a backup.

Any other failure (restore error, the server stopping during recovery) means the repository
or the keys are wrong: fix it the same day.

`make test-backup` runs the whole chain against a local MinIO (backup, rows written after it,
restore to the latest point and to an earlier moment). CI runs it; it needs Docker and no
secrets.

## Restore on the same box

Use this for a bad migration, a deleted table or a corrupted database on a box that still
runs. Pick the moment just before the damage, in UTC:

```bash
make backup-restore target="2026-10-30 14:03:00+00" CONFIRM=1
```

`make backup-restore` refuses without `CONFIRM=1`: it replaces the live database. It stops
app, worker, scheduler, the sidecar and postgres, restores over the data directory
(`--delta`, only the changed files), starts postgres, and waits until PostgreSQL leaves
recovery (bounded by `RESTORE_RECOVERY_TIMEOUT_SECONDS`, default 21600, six hours). Without
`target` it replays to the end of the archived WAL.

If the server stops during recovery, the target is past the end of the archived WAL and
the container would restart in a loop. The command fails, prints the log tail and the
command that stops the server; run it (or `make down`) and repeat with an earlier target.
`make backup-info` shows the WAL range.

The app, worker, scheduler and sidecar stay stopped. Then:

1. Check the data: `make psql`, or a quick look through the app after the next step.
2. Run `infra/deploy/deploy.sh` (`make deploy-prod`, or rerun the CD workflow) to start the rest
   of the stack.
3. Take a full backup: `make backup-now type=full`. After a restore the cluster archives
   into the same stanza on a new timeline; the full backup anchors that timeline, and
   a restore to a moment after the restore needs it.

Run the drill with the same `target` first when time allows: it proves the moment is
recoverable without touching the live database.

## Full loss: a replacement box

1. Provision the server (`make server-bootstrap`, `make server-provision`; see
   `infra/ansible/README.md`) and clone the repository.
2. Put `.env` and `.env.backup` from the password manager next to it, with
   `BACKUP_ENABLED=true`. The passphrase must be the original one.
3. Restore before the first deploy:

   ```bash
   make backup-restore CONFIRM=1
   ```

   It works on a fresh box: the restore runs the postgres service definition in a container
   of its own and needs neither a deployed stack nor a running sidecar. Add `target=...` to
   stop before a bad moment. Before the server starts, it writes the `stanza-ready` marker
   on the new volume (a restore from the stanza proves this cluster owns it), so the server
   archives from its first start, the new timeline's `.history` file included. Without that
   file in the repository a second loss would restore the old timeline to its end and drop
   everything this box wrote.
4. Check the data with `make psql`.
5. Deploy. `stanza-create` finds the stanza of this very cluster and continues it on the new
   timeline.
6. `make backup-now type=full`.

`deploy.sh` fails on a box whose volume holds a cluster other than the one in the bucket's
stanza: a fresh empty volume against an existing bucket is exactly that, and it is the reason
to restore first. Do not delete the stanza to get past it.

## A stolen bucket key

A key that leaked can read the backups (they stay encrypted unless the passphrase leaked as
well), add delete markers that hide locked versions, and delete for good the versions whose
lock has ended. Act in this order:

1. Rotate the key first: create a new access key in the Hetzner project, delete the leaked
   one, put the new pair into `.env.backup` and deploy (the database restarts, see *Enabling
   backups on a box*). Until then anything you repair the old key can undo.
2. Bring the hidden objects back. A delete marker on a versioned bucket hides the versions
   under it; deleting the marker by its version id makes the newest version current again.
   With the new key in the environment, in the same `aws-cli` container
   `scripts/ops/backup_bucket.sh` runs:

   ```bash
   REGION=<fsn1|nbg1|hel1> BUCKET=<bucket>
   s3api() {
     docker run --rm -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION="$REGION" \
       amazon/aws-cli:2.38.1 --endpoint-url "https://$REGION.your-objectstorage.com" s3api "$@" </dev/null
   }
   export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
   s3api list-object-versions --bucket "$BUCKET" --prefix pgbackrest/ \
     --query 'DeleteMarkers[?IsLatest] | [].[Key, VersionId]' --output text > markers.txt
   while read -r key version; do
     [ "$key" = None ] && continue
     s3api delete-object --bucket "$BUCKET" --key "$key" --version-id "$version"
   done < markers.txt
   ```

   `--prefix` is the repository path without its leading slash (`PGBACKREST_REPO1_PATH` when
   `.env.backup` overrides it). Do this within 10 days of the markers being added: lifecycle
   expires a version 10 days after it became noncurrent, and the hidden versions are gone for
   good after that. An object the key overwrote instead of deleting has a new locked version on
   top; copy the previous version back over it (`s3api copy-object --bucket "$BUCKET"
   --key <key> --copy-source "$BUCKET/<key>?versionId=<id>"`).
3. `make backup-info` must list the backups and a WAL range ending a minute or two ago. Run
   the drill (`make backup-verify`) and take a full backup (`make backup-now type=full`).

If the passphrase leaked too, every backup in the bucket is readable by whoever holds the
old key's copies. pgBackRest cannot re-encrypt a repository: start a new one with a new
passphrase and a new path (*A new database against an existing bucket*) and take a full
backup there.

## A new database against an existing bucket

A genuinely new database (another project, or a deliberate start from zero) must not share
a stanza with the old one: pgBackRest would refuse it, and forcing it would orphan the old
history. Give it a new repository path deliberately: `PGBACKREST_REPO1_PATH=/pgbackrest-<name>`
in that box's `.env.backup` (pgBackRest reads it over `repo1-path` in
`infra/backup/pgbackrest.conf`), or use a new bucket.

## Which tool when

| Need | Use |
| --- | --- |
| Lost box, corrupted cluster, bad migration, any moment in the last 14 days | pgBackRest: `make backup-restore` |
| Copy one database to a laptop or another server, move between PostgreSQL major versions | logical dump: `make backup` / `make restore f=...` |
| A dump taken before a risky manual change, kept beside the work | `make backup` |

`make backup` is a snapshot at one instant of one database. It cannot replay to a moment and
lives on the same box unless copied off.

## Targets

| Command | Does |
| --- | --- |
| `make backup-bucket BUCKET=... REGION=...` | applies Object Lock default and lifecycle to the bucket |
| `make backup-info` | lists backups and the WAL range |
| `make backup-now type=full\|diff` | takes a backup now, reports to no heartbeat |
| `make backup-verify [target="..."]` | restore drill in a throwaway container |
| `make backup-restore [target="..."] CONFIRM=1` | restores the live database |
| `make test-backup` | end-to-end check against a local MinIO |

The targets build the postgres image on the box first (`make backup-image`) and start
containers of their own: `backup-info` and `backup-now` run the sidecar's service definition,
`backup-restore` the postgres service, `backup-verify` a plain `docker run` on a throwaway
volume. So they work before the first deploy.
