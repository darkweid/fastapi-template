# Bootstrap: From Clone to First Request

Everything on this page is done **once**, on a fresh fork. When you reach the
end, the daily loop is the README *Quick Start* and nothing here comes back.

Order matters in one place only: rename before the first `make run`, or you
inherit a set of volumes named after this template.

---

## 1. Prerequisites

- Docker Engine with Compose v2 (`docker compose version`)
- Python 3.13 (`python3.13 --version`) — for pre-commit hooks and local scripts
- `make`, `git`

The application itself never runs outside Docker. The local virtualenv exists
for the hooks, `pip-tools` and the occasional script.

---

## 2. Rename the template

The compose project name, every container name, every image tag and both volume
names carry `template-` / `fastapi-template`. Two stacks forked from this
template on one machine collide on all of them.

Pick a slug — lowercase, no spaces — and replace:

| What | Where | Becomes |
| --- | --- | --- |
| Compose project name `fastapi-template` | `infra/docker-compose.yml:4` | `myapp` |
| Container names `template-app`, `-worker`, `-scheduler`, `-nginx`, `-postgres`, `-redis`, `-app-builder` | `infra/docker-compose.yml`, `infra/docker-compose.override.yml` | `myapp-*` |
| Prod image tag `template-app-image:latest` | `infra/docker-compose.yml` (the `APP_IMAGE` fallback, 4 services), `infra/deploy/deploy.sh:21` | `myapp-app-image:latest` |
| Dev image tag `template-app-dev-image:latest` | `infra/docker-compose.override.yml` | `myapp-app-dev-image:latest` |
| Postgres image tag `template-postgres:18` | `infra/docker-compose.yml:23` | `myapp-postgres:18` |
| Test Postgres tag `template-postgres-test:18` | `infra/docker-compose.test.yml:26` | `myapp-postgres-test:18` |
| Volume names `template-postgres-data`, `template-redis-data` | `infra/docker-compose.yml:187,189` | `myapp-postgres-data`, `myapp-redis-data` |
| Integration-suite project prefix `template-test-$$` | `Makefile:145` | `myapp-test-$$` |

If the stack has already run once, tear it down **before** renaming. `make down`
resolves the project name from `infra/docker-compose.yml`, so after the rename it
addresses a project that never existed while the old containers keep running and
keep holding the old volumes:

```bash
make down
```

One pass covers all of them:

```bash
NEW=myapp

git grep -lz -e 'template-' -e 'fastapi-template' -- Makefile infra docs \
  | xargs -0 sed -i '' -e "s/fastapi-template/${NEW}/g" -e "s/template-/${NEW}-/g"
```

`sed -i ''` is the BSD/macOS spelling; on GNU sed drop the empty argument
(`sed -i -e ...`). Verify nothing survived:

```bash
git grep -n 'template-\|fastapi-template' -- Makefile infra docs   # expect no output
```

**Volumes are the one irreversible bit.** The names are pinned explicitly, so
renaming after the stack has run once points the new names at fresh, empty
volumes while the old data sits in `template-postgres-data` untouched.

If you renamed first and the old stack is still up, address it by its original
project name — the `-p` flag overrides the `name:` inside the compose file — and
then drop the volumes, assuming nothing in them is worth keeping:

```bash
docker compose -p fastapi-template -f infra/docker-compose.yml down
docker volume rm template-postgres-data template-redis-data
```

**The Docker network is deliberately not renamed by the command above.**
`app-network` (`infra/docker-compose.yml:191-194`) is a generic name shared by
every fork on the host. Rename it too if more than one of them will ever run on
the same machine.

The rest of this template's identity lives outside compose:

- `PROJECT_NAME` in `.env` — the Swagger/OpenAPI title.
- `VERSION` in `.env`.
- README badges point at `darkweid/fastapi-template` — swap the owner/repo or
  delete the block. The Coveralls badge also needs the repository enabled at
  coveralls.io before it resolves.
- `LICENSE` — replace the copyright holder, or delete the file for a private
  project.

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

```bash
cp .env.example .env
```

Every placeholder in `.env.example` carries the marker `-not-real`.
`scripts/check_env.py` rejects any value still carrying it — but that script is
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

Generate four distinct values and paste them over the placeholders:

```bash
for key in JWT_USER_SECRET_KEY JWT_USER_VERIFY_SECRET_KEY \
           JWT_USER_RESET_PASSWORD_SECRET_KEY CSRF_SECRET_KEY; do
  printf '%s=%s\n' "$key" "$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
done
```

Every realm you add later brings its own secret fields, and the same rule
applies to them — see [auth-realms.md](auth-realms.md).

### Other credentials

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
- `TRUST_PROXY_HOSTS` — the real proxy hops. `*` is rejected at startup.
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
curl -s localhost:8000/ready/    # 503 while Postgres is unreachable
curl -s localhost:8000/health/   # always 200, per-dependency breakdown
```

Then open http://localhost:8000/docs — open while `DEBUG=true`, behind HTTP
Basic otherwise.

---

## 6. Verify the checkout

```bash
make lint             # ruff, black, mypy via pre-commit
make test             # unit suite, no Docker needed
make test-integration # optional here: throwaway PostgreSQL, needs Docker
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
on every push to `main`. The package is private by default.

*CI (stage)* is the same pipeline bound to a `stage` branch, tagging `stage`
instead of `latest`. That branch does not exist here, so the workflow stays
dormant until you create it; a project with no staging contour deletes
`stage_ci.yml` and `stage_deploy.yml`.

CD stays skipped until you arm it. Everything the deploy reads belongs to a
GitHub Environment — create `production` (and `staging`, if you run one) under
*Settings → Environments* and put the secrets and `APP_DIR` there, one set per
box. The two `*_DEPLOY_ENABLED` gates are the exception: they live in *Settings
→ Secrets and variables → Actions → Variables*, because a job-level `if` runs
before the environment resolves and would read an environment variable as empty.

| Name | Where | What it is |
| --- | --- | --- |
| `PROD_DEPLOY_ENABLED` | Repository variable | Set to `true` to arm production CD. Until then CD (prod) is skipped. |
| `STAGE_DEPLOY_ENABLED` | Repository variable | Same for CD (stage). Leave unset if the project has no staging box. |
| `APP_DIR` | Environment variable | Deploy directory on that environment's box, e.g. `/root/app`. CD fails with a named error if it is unset. |
| `SSH_PRIVATE_KEY`, `SSH_KNOWN_HOSTS`, `SSH_USER`, `SERVER_IP` | Environment secret | Access to that environment's box. Per environment, so a staging key cannot reach production. `SSH_KNOWN_HOSTS` is `ssh-keyscan <server-ip>`, verified by hand against the host key. |
| `GHCR_USER`, `GHCR_PULL_TOKEN` | Environment secret | Pull the image from GHCR on the box (classic PAT, `read:packages`). |
| `ALERT_BOT_TOKEN`, `ALERT_CHAT_ID` | Environment secret | Telegram deploy notifications. |
| `GITLEAKS_LICENSE` | Repository secret, optional | Only needed when the repository is owned by an organization. |
| `PRECOMMIT_BOT_TOKEN` | Repository secret, optional | Lets the pre-commit autoupdate workflow open PRs that trigger CI. |

Details, including how to mint `PRECOMMIT_BOT_TOKEN`, are in
[contributing.md](contributing.md).

---

## 9. First deploy

On the target box:

1. Install Docker and Compose, clone the repository.
2. Put a filled `.env` there by hand — it is never committed and CD never
   uploads one.
3. Close the host: `scp -r infra/firewall <host>:/tmp/firewall` then
   `ssh <host> 'sudo bash /tmp/firewall/harden-host.sh'`. Docker-published ports
   bypass UFW, which is why this installs a `DOCKER-USER` chain as well.
4. Terminate TLS at Nginx. The header of `infra/nginx/tls.conf.example` carries
   the exact steps, and swapping the config file is only the first of them: the
   server block reads `/etc/nginx/certs/fullchain.pem`, and the `nginx` service
   currently mounts configuration files only. Put the certificate and key under
   `infra/nginx/certs/`, mount `tls.conf.example` **over** the `app.conf` mount,
   and add the mounts those paths need:

   ```yaml
   - ./nginx/certs:/etc/nginx/certs:ro
   - certbot-webroot:/var/www/certbot:ro
   ```

   The second one belongs to the ACME challenge location; keep it only if you
   renew through certbot, and declare `certbot-webroot` under `volumes:` in the
   same file. Without the certificate mount Nginx cannot start and the first
   deploy fails at the last step.
5. `make deploy-prod` — the bootstrap path, which builds the image on the box
   because no registry image exists yet.

From then on CD runs `make deploy-image APP_IMAGE=ghcr.io/<owner>/<repo>:sha-<12>`
and the production box never compiles.

`infra/deploy/deploy.sh` validates `.env` before anything starts, brings up
Postgres and Redis, applies migrations, and only then rolls `app`, `worker` and
`scheduler`. A failed migration aborts the deploy with the previous containers
still serving.

Operational detail lives in [infra.md](infra.md); the threat model and the host
hardening in [security.md](security.md).

---

## Checklist

```
[ ] Renamed compose project, containers, images, volumes, test project prefix
[ ] Renamed app-network (only if several forks share a host)
[ ] PROJECT_NAME, VERSION, README badges, LICENSE
[ ] venv + make req-sync-dev + pre-commit install
[ ] .env copied and every -not-real placeholder replaced
[ ] Four signing secrets: 32+ chars, all four different
[ ] PUBLIC_BASE_URL, CORS_ALLOWED_ORIGINS, SMTP, cookie settings
[ ] make run-dev && make migrate && make create-admin
[ ] /live/, /ready/, /health/ answer; /docs opens
[ ] make lint && make test green
[ ] src/note copied for the first domain, then deleted
[ ] production environment holds the CD secrets and APP_DIR; PROD_DEPLOY_ENABLED=true (when a server exists)
[ ] Host hardened, TLS in place, first make deploy-prod done
```
