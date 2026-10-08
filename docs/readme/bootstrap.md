# Bootstrap: From Clone to First Request

Everything on this page is done **once**, on a fresh fork. When you reach the
end, the daily loop is the README *Quick Start* and nothing here comes back.

Order matters in two places: rename before the first `make run`, or you
inherit a set of volumes named after this template; and commit the nginx changes
of section 9 before the server clones the repository.

---

## 1. Prerequisites

- Docker Engine with Compose v2 (`docker compose version`)
- Python 3.13 (`python3.13 --version`) — for pre-commit hooks and local scripts
- `make`, `git`

The application itself never runs outside Docker. The local virtualenv exists
for the hooks, `pip-tools` and the occasional script.

---

## 2. Rename the template

The compose project name, every container name, every image tag, all three
volume names and the network's Docker name carry `template-` /
`fastapi-template` / `app-network`. Two stacks forked from this template on one
machine collide on all of them.

If the stack has already run once, tear it down **before** renaming. `make down`
resolves the project name from `infra/docker-compose.yml`, so after the rename it
addresses a project that never existed while the old containers keep running and
keep holding the old volumes:

```bash
make down
```

Pick a slug and run, once, on the fresh fork:

```bash
make init-project NAME=myapp                       # rename only
make init-project NAME=myapp TITLE="My API" SUBNET=10.20.30.0/24 DRY_RUN=1   # preview
```

`NAME` is 2-30 lowercase letters, digits and single inner hyphens, starting with
a letter, and may not contain `template`. The command edits exactly the places
below, refuses to run when any of them has uncommitted changes, and refuses a
second run (`infra/docker-compose.yml` no longer names `fastapi-template`). It
needs only `python3` and `git`, not the virtualenv of section 3, and it never
runs Docker. `DRY_RUN=1` prints the files it would edit and touches nothing.
`TITLE` sets `PROJECT_NAME` (the Swagger/OpenAPI title) in `.env.example` and in
the `.env` it creates; it may not be blank or hold `"`, `\`, `$`, a backtick or
a non-printable character. Every file is written whole through a rename and
`infra/docker-compose.yml` goes last, so a run that fails midway leaves the
project name in place: `git checkout --` the files it names and run it again.

| What | Where | Becomes |
| --- | --- | --- |
| Compose project name `fastapi-template` | `infra/docker-compose.yml` | `myapp` |
| Container names `template-worker`, `-scheduler`, `-nginx`, `-postgres`, `-redis`, `-app-builder` (the app has none: a deploy runs two of it side by side, named after the compose project) | `infra/docker-compose.yml` | `myapp-*` |
| Prod image tag `template-app-image:latest` | `infra/docker-compose.yml` (the `APP_IMAGE` fallback, 4 services), `infra/deploy/deploy.sh` | `myapp-app-image:latest` |
| Dev image tag `template-app-dev-image:latest` | `infra/docker-compose.override.yml` | `myapp-app-dev-image:latest` |
| Postgres image tag `template-postgres:18` | `infra/docker-compose.yml` | `myapp-postgres:18` |
| Test Postgres tag `template-postgres-test:18` | `infra/docker-compose.test.yml` | `myapp-postgres-test:18` |
| Volume names `template-postgres-data`, `template-redis-data`, `template-nginx-upstream` | `infra/docker-compose.yml` | `myapp-postgres-data`, `myapp-redis-data`, `myapp-nginx-upstream` |
| Integration-suite project prefix `template-test-$$` | `Makefile` | `myapp-test-$$` |
| Docker network name `app-network` (the compose key stays `app-network`) | `infra/docker-compose.yml` | `myapp-network` |
| With `SUBNET`: the subnet, gateway and `ip_range`, the default `TRUST_PROXY_HOSTS`, the gateway `deny` | `infra/docker-compose.yml`, `src/main/config.py`, `.env.example`, `infra/nginx/proxy.inc` | the new `/24`, its `.1`, its upper `/25` |

The network's Docker name always changes, so two forks on one host never share
a bridge. Its subnet moves only with `SUBNET`: two forks on one host also need
distinct subnets, and `SUBNET` must be a private `/24` outside `172.17.0.0/16`
(docker0). Pick it clear of Docker's default address pools (`172.17.0.0/16`
through `172.31.0.0/16`, `192.168.0.0/16`) and of every LAN or VPN the host
joins, or networks Docker creates later and those routes collide with it.

**Volumes are the one irreversible bit.** The names are pinned explicitly, so
renaming after the stack has run once points the new names at fresh, empty
volumes while the old data sits in `template-postgres-data` untouched.

If you renamed first and the old stack is still up, address it by its original
project name — the `-p` flag overrides the `name:` inside the compose file —
then remove the old network, which still holds the subnet (the next `make run`
otherwise fails with `Pool overlaps with other one on this address space`), and
drop the volumes, assuming nothing in them is worth keeping:

```bash
docker compose -p fastapi-template -f infra/docker-compose.yml down
docker network rm app-network
docker volume rm template-postgres-data template-redis-data template-nginx-upstream
```

When it creates `.env`, the command lists the values the deploy gate
(`scripts/ops/check_env.py`) would still refuse in it: the `-not-real`
placeholders it cannot generate (`EMAIL_PASSWORD`, `SENTRY_DSN`, the `S3_*` keys once
`S3_ENABLED` is on) and a `PUBLIC_BASE_URL` pointing at localhost. A `.env` that
already existed is left alone and not listed; the command prints the
`PROJECT_NAME` and `TRUST_PROXY_HOSTS` lines it needs instead. Then it lists
what is left by hand:

- If a stack of this checkout ever ran: the cleanup above.
- README badges and links point at `darkweid/fastapi-template` — swap the
  owner/repo or delete them. The Coveralls badge also needs the repository
  enabled at coveralls.io before it resolves.
- `LICENSE` — replace the copyright holder, or delete the file for a private
  project.
- `author: fastapi-template` in `infra/ansible/roles/*/meta/main.yml`.
- Review the diff (`git diff`) and commit it, before section 3.

---

## 3. Local environment

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip pip-tools
make req-sync-dev
pre-commit install
```

Dependencies are edited in `infra/requirements/*.in` and compiled with
`make req-compile`. Never hand-edit the `*.txt` lockfiles.

---

## 4. Fill `.env`

`make init-project` (section 2) created `.env` from `.env.example` if there was
none, with the four signing secrets and the Postgres, Redis and docs passwords
already generated and distinct, and listed the placeholders still left. It never
overwrites an existing `.env`; then it prints the `PROJECT_NAME` and
`TRUST_PROXY_HOSTS` values to copy in by hand. Without the command:
`cp .env.example .env` and generate the values below.

Every placeholder in `.env.example` carries the marker `-not-real`.
`scripts/ops/check_env.py` rejects any value still carrying it — but that script is
the *deploy* gate (`infra/deploy/deploy.sh` runs it first), not a local one. A
placeholder left in place therefore boots fine locally and blocks the first
deploy.

### Signing secrets

Four values, each at least 32 characters, and all four different:

- `JWT_USER_SECRET_KEY`
- `JWT_USER_VERIFY_SECRET_KEY`
- `JWT_USER_RESET_PASSWORD_SECRET_KEY`
- `CSRF_SECRET_KEY`

Sharing one would let a value minted for one purpose pass the check of another.
`JWTConfig.reject_shared_secrets` refuses to start when two of the three JWT keys
match, but it walks `JWTConfig`'s own fields only: `CSRF_SECRET_KEY` belongs to
`CookieConfig` and nothing compares it against them. That fourth one is on you.

`make init-project` generates them this way; by hand, generate four distinct
values and paste them over the placeholders:

```bash
for key in JWT_USER_SECRET_KEY JWT_USER_VERIFY_SECRET_KEY \
           JWT_USER_RESET_PASSWORD_SECRET_KEY CSRF_SECRET_KEY; do
  printf '%s=%s\n' "$key" "$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
done
```

Every realm you add later brings its own secret fields, and the same rule
applies to them — see [auth-realms.md](auth-realms.md).

### Other credentials

`make init-project` fills `POSTGRES_PASSWORD`, `REDIS_PASSWORD` and
`DOCS_PASSWORD`; the rest are yours.

- `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`
- `REDIS_PASSWORD`
- `EMAIL_SERVER` / `EMAIL_PORT` / `EMAIL_USER` / `EMAIL_PASSWORD` / `EMAIL_FROM_NAME`
- `DOCS_PASSWORD` — 32 characters minimum and **ASCII only**; HTTP Basic reaches
  FastAPI as ASCII, so a non-ASCII password locks the operator out with a bare
  401. Leaving `DOCS_USERNAME` and `DOCS_PASSWORD` both blank publishes no docs
  at all outside `DEBUG`; setting one without the other fails startup.

### Values that describe your project

- `PUBLIC_BASE_URL` — the **frontend** origin. Email verification and password
  reset links are built from it, never from the request host. `check_env.py`
  refuses a localhost value in a deploy.
- `EMAIL_VERIFY_PATH`, `PASSWORD_RESET_PATH` — the frontend routes those links
  point at.
- `CORS_ALLOWED_ORIGINS` — JSON list.
- `COOKIE_DOMAIN`, `COOKIE_SAMESITE`, `COOKIE_SECURE` — see the README
  *Auth Cookie & CSRF Configuration* section for the cross-origin SPA case.
- `TRUST_PROXY_HOSTS` — the real proxy hops. `*` is rejected at startup, as is
  any range wider than `/8` (IPv4) or `/32` (IPv6), `0.0.0.0/0` included. The
  default trusts loopback and `172.30.0.128/25`, the range compose assigns on
  `app-network` (pinned in `infra/docker-compose.yml`), where nginx sits. The
  network's gateway, `172.30.0.1`, stays outside it: Docker forwards
  published-port connections it proxies, every IPv6 client included, from
  there. These are the defaults: `make init-project SUBNET=` moves the subnet,
  and with it the `172.30.0.x` values here become that `/24`'s `.1` and upper
  `/25`.
- `DEBUG=false`, `VALIDATE_CERTS=true`, `COOKIE_SECURE=true` outside local
  development; `check_env.py` hard-blocks the other way round in a deploy.
- `S3_ENABLED` is `false` by default. Turn it on and fill `S3_BUCKET_NAME`,
  `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION_NAME` only if the
  project stores files; while it is off those credentials stay dormant and the
  deploy gate ignores them.
- `SENTRY_ENABLED`, `SENTRY_DSN`, `SENTRY_ENV` — Sentry is a no-op under
  `DEBUG` / `TESTING` regardless.

`.env.test` ships filled and is picked up whenever `TESTING=true`. Leave it
alone unless you change the test stack itself.

---

## 5. First run

```bash
make run-dev          # nginx on 8000, app on 8001, reload enabled
make migrate          # required — nothing migrates automatically outside deploy.sh
```

Skipping `make migrate` leaves every DB-touching request failing with
`relation "users" does not exist`.

Create the first administrator:

```bash
ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD='StrongPass1!' make create-admin
```

Check it is alive:

```bash
curl -s localhost:8000/live/     # no dependencies; what the healthcheck polls
curl -s localhost:8001/ready/    # 503 while Postgres is unreachable
curl -s localhost:8001/health/   # always 200, per-dependency breakdown
```

nginx serves `/ready/` and `/health/` only to loopback and private networks, and
never to Docker's own gateway, which is where a host-side `curl` through a
published port arrives from on Linux; port 8001 is the app itself, which the dev
stack publishes on loopback. On a server, where the app has no published port:
`docker compose -f infra/docker-compose.yml exec app wget -qO- http://127.0.0.1:8001/ready/`.

Then open http://localhost:8000/docs — open while `DEBUG=true`, behind HTTP
Basic otherwise.

---

## 6. Verify the checkout

```bash
make lint             # ruff, black, mypy via pre-commit
make test             # unit suite, no Docker needed
make test-integration # optional here: throwaway PostgreSQL and Redis, needs Docker
```

Green on a fresh clone. If not, fix that before writing any code — you are
debugging the template, not your project.

---

## 7. Make it your project

- **`src/note` is the domain template.** Flat layout with the full CRUD +
  ownership + list-query pattern and no auth baggage. Copy it for the first real
  domain. Deleting it afterwards means unwiring it in the same commit, or the
  checkout stops importing: the router in `src/main/presentation.py`, the model
  in `models/__init__.py`, the `notes` property on
  `src/core/database/uow/application.py`, and `tests/unit/src/note/`. Point each
  of those at your own domain rather than dropping the module on its own.
- **`src/user` is authentication infrastructure, not a copy source** — accounts,
  sessions, permissions. So is `src/core/auth`.
- A second class of principal (staff, partner, …) is a six-line `AuthRealm`
  declaration plus one `build_realm_auth` call — follow
  [auth-realms.md](auth-realms.md), do not copy `src/user/auth/`.
- A new SQLAlchemy model must be imported in `models/__init__.py`, or Alembic
  autogenerate will not see it.
- A new permission means two edits: a `Permission` enum member and a grant in
  `ROLE_PERMISSIONS` (`src/user/auth/permissions/`).
- A new router is mounted in `src/main/presentation.py` under `/v1`.
- A new config field means the section in `src/main/config.py` **and**
  `.env.example`, in the same commit.
- `src/system` holds the probes CI, the healthcheck and any load balancer rely
  on — leave it in place.

The layer rules (Router → UseCase → Service → Repository) live in
[architecture.md](architecture.md).

---

## 8. GitHub: CI now, CD when you are ready

CI needs no setup. *CI (prod)* authenticates to GHCR with the automatic
`GITHUB_TOKEN` and pushes `ghcr.io/<owner>/<repo>` as `sha-<12>` plus `latest`
on every push to `main` that changes code (a documentation-only push builds
nothing, see below). The package is private by default.

*CI (stage)* is the same pipeline bound to a `stage` branch, tagging `stage`
instead of `latest`. That branch does not exist here, so the workflow stays
dormant until you create it; a project with no staging contour deletes
`stage_ci.yml` and `stage_deploy.yml`.

CD stays skipped until you arm it. Everything the deploy reads belongs to a
GitHub Environment — create `production` (and `staging`, if you run one) under
*Settings → Environments* and put the secrets and `APP_DIR` there, one set per
server. The two `*_DEPLOY_ENABLED` gates are the exception: they live in *Settings
→ Secrets and variables → Actions → Variables*, because a job-level `if` runs
before the environment resolves and would read an environment variable as empty.

| Name | Where | What it is |
| --- | --- | --- |
| `PROD_DEPLOY_ENABLED` | Repository variable | Set to `true` to arm production CD. Until then CD (prod) is skipped. |
| `STAGE_DEPLOY_ENABLED` | Repository variable | Same for CD (stage). Leave unset if the project has no staging server. |
| `APP_DIR` | Environment variable | Deploy directory on that environment's server, e.g. `/srv/app`. CD fails with a named error if it is unset. |
| `SSH_PORT` | Environment variable | The server's SSH port, when it is not 22 (some providers hand servers over on 22022). |
| `SSH_PRIVATE_KEY`, `SSH_KNOWN_HOSTS`, `SSH_USER`, `SERVER_IP` | Environment secret | Access to that environment's server. Per environment, so a staging key cannot reach production. `SSH_USER` is `deploy`; `SERVER_IP` and `SSH_KNOWN_HOSTS` are printed by `make server-provision`, read from the server over a connection you verified. |
| `ALERT_BOT_TOKEN`, `ALERT_CHAT_ID` | Environment secret | Telegram deploy notifications. |
| `GITLEAKS_LICENSE` | Repository secret, optional | Only needed when the repository is owned by an organization. |
| `PRECOMMIT_BOT_TOKEN` | Repository secret, optional | Lets the pre-commit autoupdate workflow open PRs that trigger CI. |

Details, including how to mint `PRECOMMIT_BOT_TOKEN`, are in
[contributing.md](contributing.md).

### Documentation-only changes skip the pipeline

A change where every path is documentation runs the `changes` gate, `lint`
and `gitleaks`, and nothing else (a push also carries the last coverage report
forward, or runs `unit-tests` when none can be carried), and does not deploy. `lint` stays because
`make lint` runs pre-commit over every file and several of its hooks apply to
markdown: gating it would let a documentation PR merge a violation that then
fails the next code PR, on a commit that did not cause it. The `changes` job in `_ci.yml`
calls `.github/actions/docs-only-change`, which asks
`scripts/ops/docs_only_change.py` whether every changed path is documentation;
`_deploy.yml` deploys only when that run's image build succeeded. On
a private fork this is the difference between roughly three billed Actions minutes
and roughly twenty-five.

Documentation means `*.md` at any depth, anything under `docs/`, and `LICENSE`.
Everything else is code, deliberately: a wrong `true` shows up as a green PR with
nothing run, because GitHub counts a job skipped through `if:` as a passing
required check. Widen the rules in `scripts/ops/docs_only_change.py` and add the case
to `tests/unit/scripts/ops/test_docs_only_change.py` in the same commit.

---

## 9. First deploy

These steps are for a VPS. On Kubernetes or a PaaS, deploy the image CI pushes
with your platform's tooling and delete `infra/ansible` (its README says what
else goes with it).

Provision the server from your machine with Ansible - any provider, Ubuntu 24.04 or
26.04. [`infra/ansible/README.md`](../../infra/ansible/README.md) has the details.
The server clones the repository once and the first deploy runs from that clone,
so the nginx changes in steps 1 and 2 are pushed before the clone in step 5. In
short:

1. Put the API's hostname into `server_name` in `infra/nginx/app.conf` (and in
   `tls.conf.example`, in place of `api.example.com`). The default server drops
   every request for a name it does not list, so a server reached by a name missing
   from there answers nothing at all.
2. Terminate TLS at Nginx. The header of `infra/nginx/tls.conf.example` carries
   the exact steps, and swapping the config file is only the first of them: the
   server block reads `/etc/nginx/certs/fullchain.pem`, and the `nginx` service
   currently mounts the configuration directory only. Replace the content of
   `app.conf` with `tls.conf.example` (the whole `infra/nginx` directory is
   nginx's `conf.d`, so a second server file beside `app.conf` would clash with
   it), and add the mounts those paths need:

   ```yaml
   - ./nginx/certs:/etc/nginx/certs:ro
   - certbot-webroot:/var/www/certbot:ro
   ```

   The second one belongs to the ACME challenge location; keep it only if you
   renew through certbot, and declare `certbot-webroot` under `volumes:` in the
   same file. Commit and push both changes.
3. `make ansible-deps`, then copy `infra/ansible/inventory/example` to
   `inventory/production` and fill in the address, `sshd_port`, your key, the CD
   key, `app_name` and the repository URL.
4. `make server-bootstrap ENV=production` (`BOOTSTRAP_USER=ubuntu`,
   `BOOTSTRAP_PORT=22022`, `ASK_PASS=1` for the providers that need them). It
   creates `ops` and `deploy`, closes root and password login, installs Docker,
   closes the firewall - ufw plus a `DOCKER-USER` chain, since Docker-published
   ports bypass ufw - and prints a deploy key.
5. Add the deploy key to the repository (read-only), run
   `make server-provision ENV=production` to clone, put `.env` in place as the
   README shows, and copy the printed CD values into the environment.
6. Copy the certificate and key into `/srv/<app_name>/infra/nginx/certs/` on the
   server (git-ignored, so the clone has none), owned by `deploy`. Without them
   Nginx cannot start and the first deploy fails at the last step.
7. The first deploy builds the image on the server, because no registry image exists
   yet: `sudo -iu deploy bash -c 'cd /srv/<app_name> && bash infra/deploy/deploy.sh'`.

From then on CD runs `BUILD=0 APP_IMAGE=ghcr.io/<owner>/<repo>:sha-<12> bash infra/deploy/deploy.sh`
(what `make deploy-image` wraps)
and the production server never compiles.

`infra/deploy/deploy.sh` validates `.env` before anything starts, brings up
Postgres and Redis, applies migrations, and only then rolls `app`, `worker` and
`scheduler`. A failed migration aborts the deploy with the previous containers
still serving. The app rolls without downtime: the new container starts beside
the serving one and takes the traffic only once healthy.

Operational detail lives in [infra.md](infra.md); the threat model and the host
hardening in [security.md](security.md).

---

## Checklist

```
[ ] make init-project NAME=... (SUBNET=... if several forks share a host), diff committed
[ ] PROJECT_NAME, VERSION, README badges, LICENSE
[ ] venv + make req-sync-dev + pre-commit install
[ ] .env created and every -not-real placeholder it listed replaced
[ ] Four signing secrets: 32+ chars, all four different
[ ] PUBLIC_BASE_URL, CORS_ALLOWED_ORIGINS, SMTP, cookie settings
[ ] make run-dev && make migrate && make create-admin
[ ] /live/, /ready/, /health/ answer; /docs opens
[ ] make lint && make test green
[ ] src/note copied for the first domain, then deleted
[ ] production environment holds the CD secrets and APP_DIR; PROD_DEPLOY_ENABLED=true (when a server exists)
[ ] API hostname in nginx server_name and TLS config pushed, server provisioned (make server-provision), certificates copied, first deploy done
```
