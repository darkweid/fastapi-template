# Security

Overview of security mechanisms implemented in the template and the rationale behind each.

## JWT Token Architecture

`src/user/auth/security.py`, `src/main/config.py`

**Separate secret keys per token purpose:**
- `JWT_USER_SECRET_KEY` — access and refresh tokens.
- `JWT_VERIFY_SECRET_KEY` — email verification tokens.
- `JWT_RESET_PASSWORD_SECRET_KEY` — password reset tokens.
- `JWT_ADMIN_SECRET_KEY` — reserved for admin tokens.

Key compromise is isolated: leaking the reset-password key does not allow forging access tokens. Each token carries a unique JTI (JWT ID) tracked in Redis, enabling per-token revocation.

**Why it matters:** A single shared secret is a single point of failure. Separate keys follow the principle of least privilege and limit blast radius.

## Refresh Token Rotation and Reuse Detection

`src/user/auth/rotate_refresh_token.lua`, `src/user/auth/token_helpers.py`

Every refresh request atomically (via Lua script):
1. Checks if the presented JTI was already consumed (`REUSED`).
2. Validates the JTI matches the stored active token (`INVALID`).
3. Marks the old JTI as used with a 14-day TTL.
4. Deletes the active refresh key.

If a consumed token is presented again, **all user sessions are invalidated immediately**. This detects stolen refresh tokens: an attacker replaying a token that the legitimate client already rotated triggers a full session wipe.

**Why it matters:** Without reuse detection, a stolen refresh token grants indefinite access. Token family tracking turns a silent compromise into a detectable event.

## Password Hashing

`src/core/utils/security.py`

- Algorithm: **Argon2** (OWASP-recommended, memory-hard).
- Parameters: 64 MB memory, 3 iterations, 2 threads.
- Verification runs via `asyncio.to_thread()` to avoid blocking the event loop.
- `needs_password_rehash()` detects outdated hash parameters; `_rehash_password_if_needed()` in the login flow transparently upgrades hashes on successful authentication.

**Why it matters:** Argon2's memory-hardness makes GPU/ASIC brute-force impractical. Auto-rehash ensures that strengthening parameters takes effect without requiring users to reset passwords.

## Anti-Enumeration

`src/user/auth/usecases/login.py`, `reset_password_request.py`, `resend_verification.py`

All authentication endpoints return **identical responses** regardless of whether a user exists:
- Login: verifies the password against a pre-computed dummy hash (`INVALID_CREDENTIALS_PASSWORD_HASH`) when the user is not found, producing constant execution time.
- Password reset and resend verification: return `success=True` even if the email is not registered.

**Why it matters:** Timing and response differences let attackers enumerate valid accounts. Dummy-hash verification eliminates the timing side-channel; uniform responses eliminate the content side-channel.

## Email Masking in Logs

`src/core/utils/security.py` — `mask_email()`

All authentication flows log emails in masked form: `ab***@cd***`. Used consistently across login, register, reset, verify, and token refresh flows.

**Why it matters:** Unmasked emails in logs create a secondary data breach vector. Log aggregation systems, crash reporters, and monitoring dashboards are often less strictly access-controlled than the primary database.

## Rate Limiting

`src/core/limiter/depends.py`, `src/core/limiter/script.py`

Redis-backed **token bucket** via Lua script:
- Configurable per-endpoint limits (requests, time window).
- Key structure: `{prefix}:{client_ip}:{endpoint}`.
- Lua script ensures atomic increment-or-reject.

**In-memory fallback** activates on Redis failure:
- Thread-safe dictionary with lock.
- Capped at 100,000 entries (~20-25 MB).
- Oldest entries evicted when at capacity.
- State transitions (degraded/recovered) reported to Sentry.

**Why it matters:** Rate limiting is the first line of defense against brute-force, credential stuffing, and abuse. The fallback ensures protection continues during Redis outages instead of silently disabling.

## Session Management

`src/user/auth/redis_keys.py`, `src/user/auth/token_helpers.py`

Sessions are Redis-backed with a key structure: `{token_type}:{user_id}:{session_id}`.

- Each login creates a unique `session_id` (UUID4), enabling multi-device support.
- `invalidate_user_session()` — single device logout.
- `invalidate_all_user_sessions()` — full account logout using non-blocking `SCAN`.
- Logout endpoint supports both modes via `terminate_all_sessions` flag.

**Why it matters:** Stateless JWT alone cannot be revoked. Redis-backed JTI tracking adds revocation capability while preserving JWT's stateless verification for normal requests.

## RBAC (Role-Based Access Control)

`src/user/auth/permissions/`

Three-tier model:
- `Permission` enum — 42 granular permissions (view, create, edit, delete per resource).
- `UserRole` enum — `ADMIN`, `EDITOR`, `VIEWER`.
- `ROLE_PERMISSIONS` matrix — maps each role to its allowed permissions.
- `require_permission()` — FastAPI dependency that checks active + verified + permitted.

**Why it matters:** Endpoint-level auth checks (`Depends(current_user)`) only verify identity. Permission checks verify authorization, preventing horizontal and vertical privilege escalation.

## Input Validation

`src/core/schemas.py`, `src/core/validations.py`

- All Pydantic schemas inherit from `Base` with `extra="forbid"` — unknown fields are rejected, not silently ignored.
- Strong password regex: lowercase + uppercase + digit + special character, 8-128 chars, printable ASCII only.
- Email normalization (`strip().lower()`) runs before validation via `EmailNormalizationMixin`.
- Field-specific regex patterns for names, usernames, phone numbers, slugs, social handles.

**Why it matters:** `extra="forbid"` prevents mass assignment attacks (injecting fields like `is_admin=True`). Strict regex patterns reject malformed input before it reaches business logic.

## SQL Injection Prevention

`src/core/database/repositories.py`

- All database access goes through SQLAlchemy ORM — no raw SQL with user input.
- `FilterCondition` validates that filter columns exist on the model before building queries.
- `_escape_like_literal()` escapes `\`, `%`, `_` before LIKE/ILIKE operations.

**Why it matters:** ORM parameterization prevents classic SQL injection. LIKE escaping prevents a secondary vector where `%` or `_` in user input alter query semantics (e.g., `%admin%` matching unintended rows).

## Security Headers

`src/core/middleware.py`, `infra/nginx/app.conf`

Applied at both application and Nginx levels (defense in depth):

| Header | Value | Purpose |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | Prevents MIME-type sniffing |
| `X-Frame-Options` | `DENY` | Prevents clickjacking |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains; preload` | Forces HTTPS for 1 year |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Limits referrer leakage |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` | Disables unnecessary browser APIs |
| `Content-Security-Policy` | `default-src 'self'; frame-ancestors 'none'` | Restricts resource loading (relaxed for Swagger/Redoc paths) |

Nginx additionally sets `server_tokens off` (hides version) and `client_max_body_size 10m`.

**Why it matters:** Headers are a zero-cost defense layer. HSTS prevents SSL stripping, CSP mitigates XSS, X-Frame-Options blocks clickjacking. Duplicating at Nginx and app level ensures coverage even if one layer is bypassed.

## Error Handling and Information Leakage Prevention

`src/core/middleware.py`, `src/core/errors/exceptions.py`

- All domain code raises project-specific exceptions (`UnauthorizedException`, `AccessForbiddenException`, etc.) — never raw `HTTPException`.
- PostgreSQL errors are mapped to safe HTTP responses: unique violation to 409, foreign key to 400, others to generic 500.
- Unexpected errors return `"Unexpected error"` — no stack traces, no internal details.
- Request timing middleware logs only method, path, duration, and status code — no request/response bodies.
- Server errors are reported to Sentry with context for debugging.

**Why it matters:** Error messages are an information disclosure vector. Generic responses prevent attackers from inferring database schema, business logic, or technology stack from error output.

## OTP Generation

`src/core/utils/security.py`

- Uses `secrets.choice()` (cryptographically secure PRNG), not `random`.
- Numeric-only, configurable length.
- Stored in Redis as one-time tokens with TTL.
- Validated and invalidated atomically — cannot be reused.

**Why it matters:** `random` is predictable with enough samples. `secrets` uses the OS entropy source, making OTP values unpredictable even to an attacker who observes previous codes.

## Soft Delete

`src/core/database/mixins.py`, `src/core/database/repositories.py`

- `SoftDeleteMixin` adds `deleted_at` and `is_deleted` fields.
- `SoftDeleteRepository` automatically filters `is_deleted=False` on all queries.
- Deleted records are retained for audit trail and potential recovery.

**Why it matters:** Hard deletes destroy forensic evidence. Soft delete preserves an audit trail for incident investigation while keeping deleted data invisible to normal application queries.

## Docker Security

`infra/docker/Dockerfile`

- **Multi-stage build:** build dependencies are not present in the final image.
- **Slim base image:** `python:3.13-slim-bookworm` minimizes attack surface.
- **Non-root execution:** `appuser` is created and used for the runtime process.
- **No .pyc files:** `PYTHONDONTWRITEBYTECODE=1` prevents bytecode caching.

**Why it matters:** Running as root inside a container means a container escape yields root on the host. Non-root execution, minimal images, and multi-stage builds reduce both the probability and impact of container compromise.

## Nginx Hardening

`infra/nginx/app.conf`, `infra/nginx/main.conf`

- `server_tokens off` — no version disclosure.
- `client_max_body_size 10m` — prevents oversized request abuse.
- Proper proxy headers (`X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`).
- WebSocket upgrade support with secure defaults.
- Security headers duplicated from application layer.

**Why it matters:** Nginx is the outermost layer. Version disclosure aids targeted exploits. Size limits prevent memory exhaustion. Correct proxy headers ensure the application sees real client information for rate limiting and logging.

## Cache Architecture

`src/core/redis/cache/coder/`

Two coder implementations:
- **JsonCoder** — safe for any data, no code execution risk.
- **PickleCoder** — faster serialization for internal-only cache data. Appropriate only when Redis access is restricted to the application.

Tag-based invalidation (`CacheTags` enum) allows selective cache purging by resource type without full cache flushes.

**Why it matters:** Pickle deserialization can execute arbitrary code. The coder choice should match the trust level of the Redis environment. Tag-based invalidation prevents stale data from being served after mutations.

## Celery Task Security

`src/user/auth/tasks.py`, `celery_tasks/workers/common.py`

- Tasks receive only email addresses, not full user objects or tokens — tokens are created inside the task.
- Redis connections are created and destroyed per task execution.
- Failed tasks clean up throttle keys and invalidate tokens before re-raising.
- `task_time_limit=1800` prevents runaway tasks.
- `task_acks_late=True` ensures tasks are re-delivered if the worker crashes.

**Why it matters:** Celery serializes task arguments to the broker (RabbitMQ). Passing tokens or sensitive objects through the broker expands the attack surface. Creating tokens inside the task keeps sensitive material within the application boundary.
