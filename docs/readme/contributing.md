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
- Tests: generates `.env` from example and runs `make test`.

### CD (`.github/workflows/deploy.yml`)
- Deploys from `main` after successful CI.
- Runs `check_env.py`, deploys via `make deploy-prod`, cleans resources, restarts nginx.
- Notifications: Telegram with status, duration, pipeline link.

### Pre-commit Autoupdate (`.github/workflows/pre-commit-autoupdate.yml`)
- Runs weekly (Monday, `06:20 UTC`) and can be triggered manually (`workflow_dispatch`).
- Updates hook revisions in `.pre-commit-config.yaml` via `pre-commit autoupdate`.
- Syncs `mypy.additional_dependencies` in `.pre-commit-config.yaml` from pinned versions in `infra/requirements/dev.txt` via `scripts/sync_precommit_mypy_deps.py`.
- Validates resulting config with `pre-commit validate-config`.
- Creates or updates PR `chore/pre-commit-autoupdate` with labels `dependencies`, `ci`.

### Required Secrets
- SSH_PRIVATE_KEY, SERVER_IP, SSH_USER — server access.
- ALERT_BOT_TOKEN, ALERT_CHAT_ID — Telegram notifications.
- PRECOMMIT_BOT_TOKEN (optional but recommended) — token for creating autoupdate PRs so downstream workflows can run reliably.
- Production `.env` must exist on the target server.

### How to create `PRECOMMIT_BOT_TOKEN`
1. Open GitHub: `Settings -> Developer settings -> Personal access tokens -> Fine-grained tokens -> Generate new token`.
2. Set repository access to this repository (`Only select repositories`).
3. Grant repository permissions:
   - `Contents: Read and write`
   - `Pull requests: Read and write`
4. Copy the generated token.
5. Add it to repository secrets:
   - `Repo -> Settings -> Secrets and variables -> Actions -> New repository secret`
   - Name: `PRECOMMIT_BOT_TOKEN`
   - Value: your generated token
