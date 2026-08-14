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
- Key structure: `{prefix}:{client_ip}:{request_path}:{endpoint}`.
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
- `Permission` enum — 28 granular permissions (view, create, edit, delete per resource).
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

`src/core/database/repositories.py`, `src/core/database/filters.py`

- All database access goes through SQLAlchemy ORM — no raw SQL with user input.
- `FilterCondition` validates that filter columns exist on the model before building queries.
- `_escape_like_literal()` escapes `\`, `%`, `_` before LIKE/ILIKE operations.

**Why it matters:** ORM parameterization prevents classic SQL injection. LIKE escaping prevents a secondary vector where `%` or `_` in user input alter query semantics (e.g., `%admin%` matching unintended rows).

## Security Headers

`src/core/middleware.py`, `infra/nginx/app.conf`

The five base headers are applied at both the application and Nginx levels (defense in depth); `Content-Security-Policy` is applied at the application layer only (it is path-aware — relaxed for Swagger/Redoc):

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

## Host Port Exposure (Docker & UFW)

`infra/docker-compose.yml`, `infra/docker-compose.override.yml`

**Why UFW does not protect Docker-published ports:** the short port syntax
`ports: "host:container"` binds `0.0.0.0` (all interfaces), and Docker inserts
its own rules into the `DOCKER` iptables chain — which is evaluated *before* the
`INPUT` chain that UFW manages. As a result, a `ufw deny <port>` rule has **no
effect** on a container-published port: it is reachable from the internet even
when UFW reports the port as blocked.

**What the template does:**
- The base (production) compose file publishes **only Nginx (`80` and `443`)** to
  the host. Postgres, Redis, and the app stay off the host — they communicate
  over the internal `app-network` bridge by service name (`postgres:5432`,
  `redis:6379`, `app:8001`), so they are unreachable from outside the host
  regardless of firewall state.
- The dev overlay (`docker-compose.override.yml`, local-only) re-exposes those
  backing services bound to `127.0.0.1` for debugging and host-side integration
  tests. Loopback binds are not reachable from the network, so the iptables
  bypass does not apply.

**The remaining public ports (Nginx, `80`/`443`):** these are the intended front
door and are published on `0.0.0.0` by design. Because of the bypass above, UFW
alone will not govern them. `infra/firewall/` ships the policy that does: it
combines UFW for host listeners with a `DOCKER-USER` chain for container traffic
(Docker evaluates `DOCKER-USER` before every rule of its own), installed as a
systemd unit so it survives reboots and daemon restarts.

```bash
scp -r infra/firewall <host>:/tmp/firewall
ssh <host> 'sudo bash /tmp/firewall/harden-host.sh'
```

The result: `22`, `80` and `443` reachable from the internet, everything else
through an SSH tunnel only. `PUBLIC_TCP_PORTS` narrows or widens the container
side, `SSH_PORT` covers a non-default SSH port. See `infra/firewall/README.md`.

[`ufw-docker`](https://github.com/chaifeng/ufw-docker) solves the same problem by
wiring Docker traffic through UFW's `route` rules, if you would rather manage the
policy from UFW alone.

**Why it matters:** publishing Postgres/Redis on `0.0.0.0` exposes
unauthenticated-by-default data stores to the internet, silently bypassing the
host firewall. Keeping backing services off the host and constraining the one
public port via `DOCKER-USER`/`ufw-docker` closes that gap.

## Nginx Hardening

`infra/nginx/app.conf`, `infra/nginx/main.conf`, `infra/nginx/proxy.inc`

- `server_tokens off` — no version disclosure.
- `client_max_body_size 10m` — prevents oversized request abuse.
- Proper proxy headers (`X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`).
- WebSocket upgrade support with secure defaults.
- Security headers duplicated from application layer.
- `proxy.inc` holds the proxy body shared by the plain-http server and the TLS
  server in `tls.conf.example`, so the two cannot drift apart.
- `Strict-Transport-Security` is sent by the application only, never by Nginx. Two
  HSTS headers with different `max-age` or `preload` values contradict each other,
  and a browser resolves that by taking whichever arrived first — leaving how long
  clients are pinned to HTTPS up to header ordering.

**Why it matters:** Nginx is the outermost layer. Version disclosure aids targeted exploits. Size limits prevent memory exhaustion. Correct proxy headers ensure the application sees real client information for rate limiting and logging.

## Cache Architecture

`src/core/cache/`

Every cached value is serialized as JSON and decoded through a `TypeAdapter` built
from the caller's declared type (`src/core/cache/serializer.py`) — there is no
pickle path anywhere in this layer. A compromised or untrusted Redis instance can
hand back malformed JSON, which fails to decode, but it cannot make the process
execute arbitrary code the way a pickle payload could.

Invalidation is a namespace-version bump over Lua (`cache.invalidate(namespace)`),
not a key scan or a tag registry — there is no `CacheTags` enum, no `KEYS`/`SCAN`
call, and no per-entry deletion.

**Key hygiene:** a cache key's `suffix` (`CacheKey(namespace, suffix)`) must never
carry an email address, phone number, token, or other identifying value that isn't
already the endpoint's own path parameter. Key builders live per domain
(`src/user/cache_keys.py` is the reference) precisely so this rule has one place to
enforce, instead of every call site assembling its own key string.

**`CacheScope.PRIVATE` vs `CacheScope.PUBLIC`:** `@cached_route` requires an explicit
`scope`, and the two are not interchangeable safety-wise.
- `PRIVATE` requires an `identity` callback and emits `Cache-Control: private`. Use
  it whenever the response body differs by caller — the response for user A must
  never satisfy a request from user B's browser or a shared proxy sitting between
  them. If the identity callback returns `None` for a given request (caller cannot
  be identified), the decorator bypasses the cache entirely for that call rather
  than risk collapsing distinct callers onto one entry. The identity is appended to
  the cache key by the decorator itself, not by the key builder — a builder is free
  to ignore who is asking, and a `PRIVATE` entry is still per-caller.
- `PUBLIC` emits `Cache-Control: public, max-age=<ttl>`. Under RFC 9111 §3.5, a
  shared cache (a CDN, a corporate proxy, any intermediary between the client and
  this API) is normally forbidden from storing a response to a request that carried
  an `Authorization` header — unless the response explicitly says `public`. Setting
  `PUBLIC` on an authenticated endpoint is exactly that override: it tells every
  intermediary on the path that it is allowed to cache and replay this
  authorization-gated response to other clients. Every `PUBLIC` response therefore
  also carries `Vary: Authorization`, so a shared cache must key its entry by the
  credential that produced it and cannot serve a stored response to a request that
  arrived with a different `Authorization` header, or with none at all.

  `Vary` narrows the blast radius; it does not make `PUBLIC` safe by itself. It
  says nothing about callers who share one token, and it does not apply to this
  API's own Redis entry, which is shared by every permitted viewer by design.

  `GET /v1/users/{user_id}` (`src/user/routers.py`) does this deliberately: it
  requires the `VIEW_USERS` permission, but the response body is identical for
  every caller who holds that permission — there is nothing in it that varies by
  who is asking, so one shared cache entry per `user_id` is the intended behavior,
  not a leak. The template ships no CDN or shared caching proxy in front of the
  API, so today `PUBLIC` only affects `RedisCache`, which nothing outside this
  process can reach. **If a project adds a CDN or a shared caching proxy in front
  of the API, any endpoint using `PUBLIC` must be re-evaluated first** — a response
  that looks caller-independent today can stop being so after a future change to
  the endpoint (e.g. adding a per-viewer field), and at that point `PUBLIC` would
  let the shared cache serve one caller's data to another. When the response does
  vary by caller, or when in doubt, use `PRIVATE` with an `identity` callback
  instead.

**Why it matters:** JSON-only serialization removes a remote-code-execution vector
that pickle-based caches carry. Namespace versioning removes an entire class of
invalidation bugs (partial tag purges, forgotten tags) at the cost of coarser
granularity. The `PUBLIC`/`PRIVATE` distinction is what stands between "one cache
entry serves every permitted viewer" and "one user's cached response leaks to
another" once a shared cache sits on the request path.

## Taskiq Task Security

`src/user/auth/tasks.py`, `taskiq_worker/app.py`

- Tasks receive only email addresses, not full user objects or tokens — tokens are created inside the task.
- Redis connections (`get_tasks_redis_client`) are created and destroyed per task run.
- Failed tasks clean up throttle keys and invalidate tokens before re-raising.
- `SmartRetryMiddleware` retries tasks opted in via `retry_on_error=True` (the email tasks) up to a bounded number of times, with no delay between attempts - the Redis Streams broker has no delayed delivery, so retries fire back-to-back; idempotence-free tasks stay out.
- Redis Streams delivery is at-least-once: a worker XACKs a message only after the task finishes, so a worker crash between sending the email and acking can redeliver the task and duplicate the send — an accepted trade-off, not a defect.
- `infra/redis.conf` enables AOF (`appendfsync everysec`) alongside the RDB snapshots, since this Redis now also holds the task stream: an RDB-only setup can lose queued jobs written since the last snapshot on a crash.
- `taskiq_worker/broker.py`'s `STREAM_MAXLEN` bounds stream growth (acked entries are never otherwise removed), sized well above any realistic backlog for this workload — `XADD MAXLEN` trims oldest-first regardless of ack state, so a cap sized too close to real traffic could discard unacknowledged work.
- PII lifecycle: task payloads (email addresses, etc.) persist in the Redis stream until `STREAM_MAXLEN` trims them and live on disk in the AOF/RDB volume until then. Outbox rows keep the same task args in Postgres for 7 days (`outbox_purge`, `src/core/outbox/tasks.py`). Worker-side dedup markers (`taskiq:done:{task_id}`) hold no payload, just a 1-hour TTL marker.

**Why it matters:** taskiq serializes task arguments onto the Redis stream. Passing tokens or sensitive objects through it expands the attack surface. Creating tokens inside the task keeps sensitive material within the application boundary.
