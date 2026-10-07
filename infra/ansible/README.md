# Server provisioning

**For a VPS:** one or a few Ubuntu machines you rent - from Hetzner,
DigitalOcean, Vultr, Linode, OVHcloud, AWS or any other provider - and run docker
compose on. Kubernetes or a PaaS? See [Not on a VPS?](#not-on-a-vps).

`infra/ansible` takes a fresh Ubuntu 24.04 or 26.04 VM at any provider to a server
that CD can deploy to. It provisions the host and stops there: deploying stays in
`infra/deploy/deploy.sh`, which CD runs over SSH, and Ansible never writes `.env`
or moves the checkout after cloning it.

What a provisioned server has:

- `ops` - your account: sudo without a password, the keys in
  `users_ops_authorized_keys`, the only account Ansible uses after bootstrap.
- `deploy` - CD's account: no sudo, member of `docker`, owns the checkout at
  `/srv/<app_name>`, its key restricted to running commands (no pty, no
  forwarding). Membership in `docker` is root-equivalent: treat the CD key as a
  root key.
- sshd on `sshd_port` with keys only, no root login, `AllowUsers ops deploy`.
- ufw allowing SSH, 80 and 443, plus a `DOCKER-USER` chain that drops everything
  forwarded to a container except 80/443 and Docker's own bridges. Docker
  publishes ports past ufw; this chain is what filters them. It is in place
  before Docker starts at boot, and Docker does not start when it cannot be applied.
- Docker CE from Docker's repository (key shipped in `roles/docker/files`),
  `local` log driver with rotation, `live-restore`.
- Security updates installed by `unattended-upgrades`, never an automatic reboot;
  swap, UTC, a capped journal.

## Prerequisites

- Python 3.12 or newer on your machine, then `make ansible-deps` (a virtualenv in
  `infra/ansible/.venv` plus the pinned collections; `make req-sync-dev` would
  uninstall Ansible from the dev environment, hence its own).
- `sshpass`, only for a provider that hands the server over with a root password
  (`ASK_PASS=1`).
- The server's SSH host key accepted once by hand on the port the provider gave:
  `ssh -p <port> <user>@<ip>`, and compare the fingerprint with the one the
  provider's console shows. Host key checking stays on; when a run moves sshd to
  another port, it adds the same keys under that port to your `known_hosts`
  itself, read over the connection you verified.
- If the provider has a firewall or security group of its own, it allows
  `sshd_port`, 80 and 443.

## Set up an environment

```bash
cp -r infra/ansible/inventory/example infra/ansible/inventory/production
```

Edit `inventory/production/hosts.yml` (the address) and
`inventory/production/group_vars/all.yml`:

| Variable | What to put there |
| --- | --- |
| `sshd_port` | The port sshd ends up on. Usually the one the provider gave (22, or 22022 at Spaceship). |
| `users_ops_authorized_keys` | Your public key(s). Each one grants root. |
| `users_deploy_authorized_keys` | The public half of the `SSH_PRIVATE_KEY` CD uses: `ssh-keygen -t ed25519 -N '' -C cd-deploy -f cd_deploy_key`, the contents of `cd_deploy_key` go into that secret. |
| `app_name` | The checkout lands in `/srv/<app_name>`. |
| `app_checkout_repo_url` | The repository's SSH URL. |
| `app_checkout_repo_host_keys` | The git server's host keys; the example holds GitHub's. |

Commit the inventory: it holds addresses and public keys, never a secret. A
change to `users_ops_authorized_keys` followed by one `make server-provision`
grants root, so give `infra/ansible/inventory/**` a required reviewer in
`CODEOWNERS`.

Optional settings are the role defaults in `roles/*/defaults/main.yml`: swap
size, Docker version pin, log rotation, trusted interfaces for a provider's
private network, `users_deploy_allowed_from` (a self-hosted runner's fixed
address only - GitHub-hosted runners leave from a rotating range).

## First run

```bash
make server-bootstrap ENV=production                          # root with a key
make server-bootstrap ENV=production BOOTSTRAP_USER=ubuntu    # AWS, GCP, ...
make server-bootstrap ENV=production BOOTSTRAP_PORT=22022     # provider port differs from sshd_port
make server-bootstrap ENV=production ASK_PASS=1               # root with a password
make server-bootstrap ENV=production BOOTSTRAP_USER=admin ASK_BECOME=1  # sudo asks for a password
```

Bootstrap creates `ops`, logs in as `ops` over a fresh connection and becomes
root; only then does it empty the provider account's `authorized_keys` (its
password stays, so the provider's web console remains a way in). It then runs
`site.yml`, which closes root and password login.

The first run ends with a failure on purpose: it prints a deploy key generated
on the server. Add it to the repository as a read-only deploy key (GitHub: Settings
-> Deploy keys) and run `make server-provision ENV=production`; that run clones.

Then:

1. Put `.env` in place once the clone exists: `scp -P <sshd_port> .env ops@<host>:`
   and on the server
   `sudo install -o deploy -g deploy -m 0600 .env /srv/<app_name>/.env && rm .env`.
2. Copy the values the summary printed into the GitHub environment: `SSH_USER`,
   `SERVER_IP`, `SSH_KNOWN_HOSTS` as secrets, `SSH_PORT` and `APP_DIR` as
   variables. `SSH_KNOWN_HOSTS` comes from the server over the connection you already
   verified, so no `ssh-keyscan`.
3. First deploy, building on the server: `ssh -p <sshd_port> ops@<host>`, then
   `sudo -iu deploy bash -c 'cd /srv/<app_name> && bash infra/deploy/deploy.sh'`.

## Later

- `make server-provision ENV=production` - converge again after changing the
  inventory or the roles; a run on an unchanged server changes nothing.
  `CHECK=1` shows what it would change, a port move included when
  `CURRENT_SSH_PORT` is given too.
- `make server-reboot ENV=production` - reboot, then wait until `DOCKER-USER` is
  in place and every service with a restart policy is running, and healthy where
  it has a healthcheck. A provisioning run says so when a reboot is due.
- Working in the checkout: `sudo -iu deploy`.
- `~/.ssh/config` for the server:

  ```
  Host myapp-prod
      HostName 203.0.113.10
      Port 22
      User ops
      IdentitiesOnly yes
      IdentityFile ~/.ssh/id_ed25519
  ```

  `IdentitiesOnly` stops your agent from offering every key it holds before
  the right one.
- Reaching Postgres or Redis on the server: `ssh -L 5432:127.0.0.1:5432 myapp-prod`
  (and `6379` for Redis). Both are published on the server's loopback only.

## Changing the SSH port

Set the new `sshd_port`, check that the provider's own firewall allows it, and run
`make server-provision ENV=production CURRENT_SSH_PORT=<the port sshd listens on
now>`. The run refuses a port another service already listens on. It opens the
new port in ufw and has sshd listen on both, trusts the host keys under the new
port, logs in on it, and only then drops the old one from sshd and from ufw. If
the new port cannot be reached from where you are, the run fails at that login
and the old port keeps working: set `sshd_port` back to the old port and run
`make server-provision ENV=production` without `CURRENT_SSH_PORT` to close the new
one again. Update `SSH_PORT` and `SSH_KNOWN_HOSTS` in the GitHub environment from
the summary.

## Locked out

If a run or a hand edit leaves you unable to log in, get a root shell through the
provider: its web console where the account has a password (images that log in by
key alone often have none, so set one before bootstrap if you want this way in),
otherwise its rescue system. Then remove
`/etc/ssh/sshd_config.d/00-hardening.conf`, run `systemctl daemon-reload` and
`systemctl restart ssh.socket ssh.service` (plain `systemctl restart ssh` where
sshd is not socket-activated: `systemctl is-enabled ssh.socket`) and
`ufw allow 22/tcp`: sshd is back on 22, which ufw no longer allows. Fix the
inventory and run `make server-provision ENV=production CURRENT_SSH_PORT=22`,
which moves sshd to `sshd_port` and closes 22 again.

## Tests

The `Ansible` workflow runs only when `infra/ansible/` (outside its markdown),
`infra/requirements/ansible.*`, the `Makefile` (its targets are what the jobs run)
or the workflow change. It lints with
`ansible-lint --profile production`, then converges a GitHub-hosted runner as if
it were a fresh server: bootstrap, a second run that must change nothing, and the
checks in `tests/ci/verify.sh`. The runner's sshd starts as a cloud image ships
it: socket-activated, with a `Port 22` line in `sshd_config` and its host key
trusted on 22 alone. Bootstrap moves it to 22022 on the way. The scripts after it
cover a `CHECK=1` preview (`check_mode.sh`), a provider drop-in that adds a port
(`provider_dropin.sh`), a run before the deploy key is added
(`missing_deploy_key.sh`), and port moves (`port_change.sh`): to a port it cannot
reach, onto a port another service holds, and to a new port and back. It never
runs on a self-hosted runner: it hardens the machine it runs on
(`tests/unit/test_ansible_workflow.py`).

Two things CI cannot do, so check them by hand on a throwaway VM before relying on
a change to them: `reboot.yml`, and Ubuntu 26.04 until GitHub publishes a 26.04
runner. The checklist: bootstrap as the provider's user, ideally on a non-22
port; provision twice (the second reports `changed=0`); add the deploy key and
provision again; deploy once as `deploy` (step 3 above); `make server-reboot`
and see the stack come back healthy with `iptables -S DOCKER-USER` ending in
`DROP` and the unreachable `RETURN` after it. On 26.04 also confirm that `sudo`
(sudo-rs there) accepts Ansible's become and that `visudo -cf` validated
`/etc/sudoers.d/ops`.

## Not on a VPS?

On Kubernetes or a PaaS the platform owns the machine, its users, SSH and its
firewall, so none of this applies. Delete `infra/ansible/`,
`infra/requirements/ansible.*`, `.github/workflows/ansible.yml`,
`tests/unit/test_ansible_workflow.py` and the `##@ Server` group with its
variables in the `Makefile`, then drop `ansible` from `REQ_NAMES` there and the
`ansible.txt` lines of the `pip-audit` job in `.github/workflows/_ci.yml`;
`git grep -i ansible` shows what is left to tidy. `infra/deploy/deploy.sh` and CD
never call Ansible, so deploys keep working.

On a VPS, keep it or replace it with something that does the same job: Docker
publishes container ports past UFW, and without the `DOCKER-USER` chain the
`firewall` role installs, nothing filters them.
