# Contributing and CI/CD

## How to Contribute
1. Fork and branch: `git checkout -b feature/your-feature`.
2. Follow typing, linters.
3. Run checks: `make lint`, `make test` (or `make test-cov` for coverage).
4. Commit and open a PR with a clear description.

## CI/CD Pipelines (GitHub Actions)

### CI (`.github/workflows/ci.yml`)
- Caching: venv by `infra/requirements.txt` hash, pre-commit, deps.
- Quality: `make check-lint`, Alembic head check.
- Tests: generates `.env` from example and runs `make test-cov`.
- Security: separate `bandit`, `pip-audit`, and `gitleaks` jobs run security checks.
- Dependency audit uses pinned files `infra/requirements/base.txt`, `infra/requirements/dev.txt`, and `infra/requirements/prod.txt` instead of floating installs.
- `gitleaks` keeps history scanning enabled and relies on a narrow repo allowlist only for known example/test placeholders.
- Security jobs are expected to fail on real findings, so dependency bumps should keep lockfiles current.

- On a push to `main` (never on a pull request) the `build-and-push` job builds `infra/docker/Dockerfile` and pushes it to GHCR as `sha-<12>` plus `latest`, with buildx layer caching and an `org.opencontainers.image.revision` label. The image is built here so a broken Dockerfile fails in CI, not halfway through a production deploy.

### CD (`.github/workflows/deploy.yml`)
- Runs after CI succeeds on a push to `main`, and pulls the exact `sha-` image that run produced — the box never builds.
- On the server it checks out the deployed commit and runs `infra/deploy/deploy.sh` with `BUILD=0`: validate `.env`, pull the image, start Postgres/Redis, apply migrations, then roll `app`, `worker`, `scheduler` and restart nginx. A failed migration aborts the deploy with the previous containers still serving.
- `concurrency: deploy` with `cancel-in-progress: false` — two merges never run two migrations at once.
- SSH host keys come from the `SSH_KNOWN_HOSTS` secret; the pipeline does not keyscan at runtime and does not disable host key checking.
- Notifications: Telegram with status, duration, pipeline link.

### Release (`.github/workflows/release.yml`)
- Pushing a `vX.Y.Z` tag builds the image again, pushes it as `vX.Y.Z` only — the `sha-` tag CI published stays immutable — and opens a GitHub Release with generated notes.
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
- SSH_PRIVATE_KEY, SERVER_IP, SSH_USER — server access.
- SSH_KNOWN_HOSTS — output of `ssh-keyscan <server-ip>`, generated once by hand and verified against the host's own key.
- GHCR_USER, GHCR_PULL_TOKEN — the server's pull credentials for GHCR (a classic PAT with `read:packages`). The package is private by default; make it public only if the application image may be world-readable.
- ALERT_BOT_TOKEN, ALERT_CHAT_ID — Telegram notifications.
- PRECOMMIT_BOT_TOKEN (optional but recommended) — token for creating autoupdate PRs so downstream workflows can run reliably.
- Production `.env` must exist on the target server.

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
