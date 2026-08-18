# Contributing and CI/CD

## How to Contribute
1. Fork and branch: `git checkout -b feature/your-feature`.
2. Follow typing, linters.
3. Run checks: `make lint`, `make test` (or `make test-cov` for coverage).
4. Commit and open a PR with a clear description.

## CI/CD Pipelines (GitHub Actions)

### CI (`.github/workflows/_ci.yml`, called by `prod_ci.yml` / `stage_ci.yml`)
- Caching: venv by `infra/requirements.txt` hash, pre-commit, deps.
- Quality: `make lint`, Alembic head check.
- Tests: generates `.env` from example and runs `make test-cov`.
- Security: separate `bandit`, `pip-audit`, and `gitleaks` jobs run security checks.
- Dependency audit uses pinned files `infra/requirements/base.txt`, `infra/requirements/dev.txt`, and `infra/requirements/prod.txt` instead of floating installs.
- `gitleaks` keeps history scanning enabled and relies on a narrow repo allowlist only for known example/test placeholders.
- Security jobs are expected to fail on real findings, so dependency bumps should keep lockfiles current.

- On a push to `main` the `build-and-push` job builds `infra/docker/Dockerfile` and pushes it to GHCR as `sha-<12>` plus the moving `latest` tag; a push to `stage` does the same with the moving `stage` tag instead. Never on a pull request. Buildx layer caching and an `org.opencontainers.image.revision` label are attached either way. The image is built here so a broken Dockerfile fails in CI, not halfway through a deploy.

### CD (`.github/workflows/_deploy.yml`, called by `prod_deploy.yml` / `stage_deploy.yml`)
- Off until you opt in: each caller is gated on its own repository **variable** — `PROD_DEPLOY_ENABLED=true` for `main`, `STAGE_DEPLOY_ENABLED=true` for `stage`. These are variables, not secrets: the `secrets` context cannot be read in a job-level `if`, and an environment's variables are only resolved after the job starts, so a gate stored there would silently evaluate to empty. Without them a fresh fork would fail a deploy against unset SSH secrets on every merge.
- Runs after `CI (prod)` succeeds on a push to `main`, and separately after `CI (stage)` succeeds on a push to `stage`; each pulls the exact `sha-` image that run produced — the box never builds.
- On the server it checks out the deployed commit and runs `infra/deploy/deploy.sh` with `BUILD=0`: validate `.env`, pull the image, start Postgres/Redis, apply migrations, then roll `app`, `worker`, `scheduler` and restart nginx. A failed migration aborts the deploy with the previous containers still serving.
- `concurrency: deploy-<environment>` with `cancel-in-progress: false` — two merges within the same environment never run two migrations at once, and production and staging queue independently of each other.
- SSH host keys come from the `SSH_KNOWN_HOSTS` secret; the pipeline does not keyscan at runtime and does not disable host key checking.
- Notifications: Telegram with status, duration, pipeline link.

### Release (`.github/workflows/release.yml`)
- Pushing a `vX.Y.Z` tag publishes the image under that tag and opens a GitHub Release with generated notes, the image digest and where the image came from.
- Build once, promote many: the release does **not** rebuild. `docker buildx imagetools create` copies the `sha-<12>` image CI already built for that commit onto the `vX.Y.Z` tag, by digest and inside the registry, so `vX.Y.Z` is bit-for-bit the artifact CI tested and CD deployed. A rebuild would run the same code on whatever base layers exist today.
- Because the digest is preserved, the version lives in the tag and the release, not in an image label — rewriting a label would change the config blob and therefore the digest.
- Tag a commit on `main` that passed CI. A tag off a branch (or predating the build job) has no `sha-` image; the workflow then falls back to building from source and says so in the release notes. It never re-pushes `sha-<12>`, which CI owns.
- Releases publish images only. Deployment still follows `main`; to run a release image, deploy it explicitly with `make deploy-image APP_IMAGE=ghcr.io/<owner>/<repo>:vX.Y.Z`.

### Pre-commit Autoupdate (`.github/workflows/pre-commit-autoupdate.yml`)
- Runs weekly (Monday, `06:20 UTC`) and can be triggered manually (`workflow_dispatch`).
- Updates hook revisions in `.pre-commit-config.yaml` via `pre-commit autoupdate`.
- Syncs `mypy.additional_dependencies` in `.pre-commit-config.yaml` from pinned versions in `infra/requirements/dev.txt` via `scripts/sync_precommit_mypy_deps.py`.
- Validates resulting config with `pre-commit validate-config`.
- Creates an autoupdate PR from a timestamped branch (`chore/pre-commit-autoupdate-*`) with labels `dependencies`, `ci`.
- When a new autoupdate PR is created, closes superseded open autoupdate PRs and tries to delete their branches.
- If posting a "superseded" comment fails, the workflow still proceeds to close the superseded PR.

### Required Secrets
CI and Release need none of the deployment secrets: both authenticate to GHCR with the automatic `GITHUB_TOKEN`. Everything below belongs to the CD path, and CD stays skipped until its `*_DEPLOY_ENABLED` variable is set.

**Repository variables** (`Settings -> Secrets and variables -> Actions -> Variables`):
- PROD_DEPLOY_ENABLED, STAGE_DEPLOY_ENABLED — set to `true` to arm the matching CD caller.

**Repository secrets** (shared by both environments):
- GITLEAKS_LICENSE — required because the repository is owned by an organization; the action refuses to scan without it. Add the same value under `Settings -> Secrets and variables -> Dependabot`, or Gitleaks fails on every Dependabot pull request: Dependabot events do not receive ordinary Actions secrets.
- ALERT_BOT_TOKEN, ALERT_CHAT_ID — Telegram notifications. One chat serves both environments; the message carries the environment in its first line.
- PRECOMMIT_BOT_TOKEN (optional but recommended) — token for creating autoupdate PRs. It has to be a repository secret, not an environment one: `pre-commit-autoupdate.yml` declares no `environment:`, so a secret scoped to `production`/`staging` would be invisible to it.

**Per-environment** (`Settings -> Environments`, one `production` and one `staging`):
- SSH_PRIVATE_KEY, SERVER_IP, SSH_USER — server access.
- SSH_KNOWN_HOSTS — output of `ssh-keyscan <server-ip>`, generated once by hand and verified against the host's own key.
- GHCR_USER, GHCR_PULL_TOKEN — the server's pull credentials for GHCR (a classic PAT with `read:packages`). The package is private by default; make it public only if the application image may be world-readable.
- APP_DIR (variable, not secret) — the checkout directory on that box, e.g. `/root/app`.

Each target server also needs its own `.env` in place before the first deploy — `infra/deploy/deploy.sh` validates it but does not create it.

### How to create `PRECOMMIT_BOT_TOKEN`
1. Open GitHub: `Settings -> Developer settings -> Personal access tokens -> Fine-grained tokens -> Generate new token`.
2. Set repository access to this repository (`Only select repositories`).
3. Grant repository permissions:
   - `Contents: Read and write`
   - `Issues: Read and write`
   - `Pull requests: Read and write`
4. Copy the generated token.
5. Add it to repository secrets:
   - `Repo -> Settings -> Secrets and variables -> Actions -> New repository secret`
   - Name: `PRECOMMIT_BOT_TOKEN`
   - Value: your generated token
