# Contributing and CI/CD

## How to Contribute
1. Fork and branch: `git checkout -b feature/your-feature`.
2. Follow typing, linters.
3. Run checks: `make lint`, `make test`.
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

### Required Secrets
- SSH_PRIVATE_KEY, SERVER_IP, SSH_USER — server access.
- ALERT_BOT_TOKEN, ALERT_CHAT_ID — Telegram notifications.
- Production `.env` must exist on the target server.
