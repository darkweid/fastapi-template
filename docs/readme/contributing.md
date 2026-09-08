# Contributing and CI/CD

## How to Contribute
1. Fork and branch: `git checkout -b feature/your-feature`.
2. Follow typing, linters.
3. Run checks: `make lint`, `make test` (or `make test-cov` for coverage).
4. Commit and open a PR with a clear description.

## CI/CD Pipelines (GitHub Actions)

### CI (`.github/workflows/_ci.yml`)
- All the jobs live in `_ci.yml`, a `workflow_call` workflow. Two thin callers bind it to a branch and a name: `prod_ci.yml` (*CI (prod)*, push and pull request on `main`, moving tag `latest`) and `stage_ci.yml` (*CI (stage)*, push and pull request on `stage`, moving tag `stage`). Edit the pipeline in `_ci.yml`; the callers only carry the trigger, the `push_image` / `moving_tag` inputs and the concurrency group. The `stage` branch does not exist in the template, so *CI (stage)* stays dormant until a fork creates it.
- CD subscribes to a caller's workflow **name** (`workflows: ["CI (prod)"]`), so renaming a caller silently stops its deploys.
- Callers pass `GITLEAKS_LICENSE` explicitly rather than `secrets: inherit` — CI runs third-party actions on every push and pull request, and needs none of the deploy secrets.
- Caching: venv by `infra/requirements.txt` hash, pre-commit, deps.
- Quality: `make lint`, Alembic head check.
- Tests: generates `.env` from example and runs `make test-cov`.
- Security: separate `bandit`, `pip-audit`, and `gitleaks` jobs run security checks.
- Dependency audit uses pinned files `infra/requirements/base.txt`, `infra/requirements/dev.txt`, and `infra/requirements/prod.txt` instead of floating installs.
- `gitleaks` keeps history scanning enabled and relies on a narrow repo allowlist only for known example/test placeholders.
- Security jobs are expected to fail on real findings, so dependency bumps should keep lockfiles current.

- On a push (never on a pull request) the `build-and-push` job builds `infra/docker/Dockerfile` and pushes it to GHCR as `sha-<12>` plus the caller's moving tag — `latest` from prod, `stage` from stage — with buildx layer caching and an `org.opencontainers.image.revision` label. The image is built here so a broken Dockerfile fails in CI, not halfway through a production deploy. A staging build never moves `latest`.

### CD (`.github/workflows/_deploy.yml`)
- Same split: `_deploy.yml` holds the deploy, `prod_deploy.yml` (*CD (prod)*) and `stage_deploy.yml` (*CD (stage)*) bind it to a GitHub Environment (`production` / `staging`) and to the branch its `workflow_run` trigger accepts (`main` / `stage`).
- Off until you opt in: each caller is gated on a repository **variable**, `PROD_DEPLOY_ENABLED=true` and `STAGE_DEPLOY_ENABLED=true` (a variable, not a secret, and a repository one rather than an environment one — a job-level `if` can read neither `secrets` nor the environment, which resolves only after the job starts). Without it a fresh fork would fail a deploy against unset SSH secrets on every merge.
- `workflow_run` always executes the copy of the caller that sits on the default branch, so an edit to `prod_deploy.yml` or `stage_deploy.yml` takes effect only once it is merged into `main`. The CI callers run from their own branch.
- Runs after its CI caller succeeds on a push to that branch, and pulls the exact `sha-` image that run produced — the box never builds.
- Server access, GHCR pull credentials, Telegram tokens and `APP_DIR` come from the job's environment, so a staging key cannot reach production. Missing SSH secrets and an unset `APP_DIR` are named in an error before anything connects, instead of surfacing as an opaque `ssh` failure. The job takes `permissions: contents: read` and nothing more: it authenticates over SSH and to GHCR from secrets, and the workflow token is used by nothing but `actions/checkout`.
- On the server it checks out the deployed commit and runs `infra/deploy/deploy.sh` with `BUILD=0`: validate `.env`, pull the image, start Postgres/Redis, apply migrations, then roll `app`, `worker`, `scheduler` and restart nginx. A failed migration aborts the deploy with the previous containers still serving.
- `concurrency: deploy-<environment>` with `cancel-in-progress: false` — two merges never run two migrations against one database at once, and the two environments queue independently.
- SSH host keys come from the `SSH_KNOWN_HOSTS` secret; the pipeline does not keyscan at runtime and does not disable host key checking.
- Notifications: Telegram with status, duration, pipeline link.

### Release (`.github/workflows/release.yml`)
- Pushing a `vX.Y.Z` tag publishes the image under that tag and opens a GitHub Release with generated notes, the image digest and where the image came from.
- Build once, promote many: the release does **not** rebuild. `docker buildx imagetools create` copies the `sha-<12>` image CI already built for that commit onto the `vX.Y.Z` tag, by digest and inside the registry, so `vX.Y.Z` is bit-for-bit the artifact CI tested and CD deployed. A rebuild would run the same code on whatever base layers exist today.
- Because the digest is preserved, the version lives in the tag and the release, not in an image label — rewriting a label would change the config blob and therefore the digest.
- Tag a commit on `main` that passed CI. A tag off a branch (or predating the build job) has no `sha-` image; the workflow then falls back to building from source and says so in the release notes. It never re-pushes `sha-<12>`, which CI owns.
- Releases publish images only. Deployment still follows `main`; to run a release image, deploy it explicitly with `make deploy-image APP_IMAGE=ghcr.io/<owner>/<repo>:vX.Y.Z`.

### Pre-commit Autoupdate (`.github/workflows/pre-commit-autoupdate.yml`)
- Runs monthly (3rd of the month, `06:20 UTC`) and can be triggered manually (`workflow_dispatch`).
- Updates hook revisions in `.pre-commit-config.yaml` via `pre-commit autoupdate`.
- Syncs `mypy.additional_dependencies` in `.pre-commit-config.yaml` from pinned versions in `infra/requirements/dev.txt` via `scripts/sync_precommit_mypy_deps.py`.
- Validates resulting config with `pre-commit validate-config`.
- Creates an autoupdate PR from a timestamped branch (`chore/pre-commit-autoupdate-*`) with labels `dependencies`, `ci`.
- When a new autoupdate PR is created, closes superseded open autoupdate PRs and tries to delete their branches.
- If posting a "superseded" comment fails, the workflow still proceeds to close the superseded PR.

### Required Secrets
CI and Release need none of these: both authenticate to GHCR with the automatic `GITHUB_TOKEN`. Everything below belongs to the CD path, and each contour's CD stays skipped until its `*_DEPLOY_ENABLED` variable is set.

Everything the deploy itself reads is an **environment** secret or variable, under `Settings -> Environments -> production` (and `staging`) — one set per box, so a staging key cannot reach production. The two gates are the exception and stay repository variables.

- PROD_DEPLOY_ENABLED, STAGE_DEPLOY_ENABLED — repository *variables* (`Settings -> Secrets and variables -> Actions -> Variables`), set to `true` to arm that contour's CD.
- APP_DIR — environment *variable*: the deploy directory on that box, e.g. `/root/app`. CD fails with a named error if it is unset.
- SSH_PRIVATE_KEY, SERVER_IP, SSH_USER — environment secrets, server access.
- SSH_KNOWN_HOSTS — environment secret, the output of `ssh-keyscan <server-ip>`, generated once by hand and verified against the host's own key.
- GHCR_USER, GHCR_PULL_TOKEN — environment secrets, the server's pull credentials for GHCR (a classic PAT with `read:packages`). The package is private by default; make it public only if the application image may be world-readable.
- ALERT_BOT_TOKEN, ALERT_CHAT_ID — environment secrets, Telegram notifications. The alert names its contour, so both environments may share a chat.
- GITLEAKS_LICENSE (optional) — repository secret, needed only when the repository is owned by an organization; without it `gitleaks-action` exits before scanning.
- PRECOMMIT_BOT_TOKEN (optional but recommended) — repository secret, token for creating autoupdate PRs so downstream workflows can run reliably.
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
