# Refresh Token Rotation Flow

This document describes how refresh token rotation works step by step. Rotation and
reuse detection are unchanged by the cookie transport described below; only the
*shape* of the response (and where the client must present the token) is new.

## Transport: cookie vs body

`login`, `login/refresh` and `logout` accept an `X-Token-Transport` request header
(`src/user/auth/token_transport.py`), resolved by `get_token_transport`:

- Absent or `cookie` (the default) — the response's `refresh_token` field is
  stripped, and the refresh token is instead written to an httponly cookie. This is
  the browser path.
- `body` — no `Set-Cookie` header is written at all, and the refresh token stays in
  the JSON body. Native mobile/desktop clients that manage their own token storage
  send this.

The header only decides how the *response* is shaped. It has no effect on how an
*incoming* refresh token is read: `read_refresh_credentials`
(`src/user/auth/dependencies.py`) always checks the `refresh_token` cookie first and
the `Authorization` header second, and CSRF (see below) is verified whenever the
token actually came from the cookie. A client cannot skip the CSRF check by sending
`X-Token-Transport: body` on a request that still carries the cookie — the source of
the token is a fact about the request, not a client-declared transport.

`TokenCookieResponder` (`src/user/auth/cookies.py`) owns both cookies:

| Cookie | Name | `httponly` | Purpose |
|---|---|---|---|
| Refresh token | `refresh_token` | yes | Carries the refresh token; never readable from JS. |
| CSRF token | `csrf_token` | no | Carries the CSRF signature (see below); read by client-side JS and echoed back in a header. |

Both cookies are scoped with `path=/v1/users/auth/login/refresh` — the browser only
attaches them on refresh requests, never on ordinary API calls — and share the
`max_age`, `domain`, `secure` and `samesite` policy from `CookieConfig`
(`src/main/config.py`; see the four `COOKIE_*`/`CSRF_SECRET_KEY` settings in
`README.md`). `logout` calls `TokenCookieResponder.clear`, which expires both
cookies; this is a no-op for a client that never received them (`body` transport).

## CSRF: stateless signed double submit

Because the refresh cookie is httponly, a same-site form or script cannot read it —
but a browser still attaches it automatically to any request to the cookie's path,
which is exactly the cross-site-request-forgery risk a double-submit cookie defends
against. The scheme (`src/user/auth/csrf.py`) is stateless — no server-side CSRF
storage:

- On login/refresh, the server computes
  `csrf_token = hmac_sha256(CSRF_SECRET_KEY, refresh_token)` and sets it as the
  readable `csrf_token` cookie, alongside the httponly `refresh_token` cookie.
- The client reads `csrf_token` from `document.cookie` and echoes it back in the
  `X-CSRF-Token` request header on the next refresh call.
- `TokenCookieResponder.verify_csrf` recomputes the HMAC from the refresh token
  actually presented and compares it against the header with `hmac.compare_digest`.
  A missing or mismatched header raises `AccessForbiddenException` (403); both
  failure modes produce the same message, so a caller can't learn which half of the
  pair was wrong.
- Binding the signature to the specific refresh token means rotation automatically
  retires the old CSRF token — there is nothing to invalidate separately.

The check applies **only** to a refresh token that arrived via cookie
(`verify_csrf` in `src/user/auth/dependencies.py` short-circuits when
`credentials.from_cookie` is `False`). A native client sending the refresh token in
the `Authorization` header needs no CSRF token: browsers do not attach arbitrary
headers to cross-site requests, so there is nothing for a forged request to replay.

**Status codes on refresh:** no refresh credentials found at all (no cookie, no
`Authorization` header) → 401 (`UnauthorizedException`); credentials present but the
CSRF check fails → 403 (`AccessForbiddenException`). The 401 case is a behavior
change from the previous `APIKeyHeader(auto_error=True)`-driven 403.

## 1) Token issuance (login)
- `LoginUserUseCase` creates a new `session_id` for the user session.
- `create_access_token` issues an access token with `mode=access_token`, `jti`, `session_id` and stores `jti` in Redis under `access:<user_id>:<session_id>` with TTL = `ACCESS_TOKEN_EXPIRE_MINUTES`.
- `create_refresh_token` issues a refresh token with `mode=refresh_token`, `jti`, `session_id` and stores `jti` under `refresh:<user_id>:<session_id>` with TTL = `REFRESH_TOKEN_EXPIRE_MINUTES`.

## 2) Incoming refresh request
- Endpoint `POST /v1/users/auth/login/refresh` resolves the refresh token via `get_refresh_credentials` (cookie first, `Authorization` header second — see "Transport: cookie vs body" above), runs the CSRF gate (`verify_csrf`) when the token came from the cookie, then uses `get_access_by_refresh_token` to decode the token.
- `verify_jti`:
  - Strips `Bearer` prefix if present and decodes JWT with `JWT_USER_SECRET_KEY`.
  - Extracts `jti`, `mode`, `sub` (user_id), `session_id`.
  - For refresh tokens:
    - Checks `used:<user_id>:<jti>`; if it exists, all user sessions are invalidated and 401 is returned (“Token reuse detected”).
  - Verifies the active JTI in Redis: key `<mode_without_suffix>:<user_id>:<session_id>` must equal the token JTI; otherwise 401 (“Token invalidated or expired”).
- Dependency returns `(user, payload)` to the use case.

## 3) Domain checks before rotation
- `GetTokensByRefreshUserUseCase` receives the current user from dependency.
  - If blocked → `PermissionDeniedException`.
  - If not verified → `InstanceProcessingException`.

## 4) Rotation execution
- `rotate_refresh_token`:
  - `validate_token_structure` ensures `sub`, `session_id` and `jti` are present; on failure invalidates all sessions.
  - `execute_token_rotation` runs a Lua script with keys `refresh:<user_id>:<session_id>` and `used:<user_id>:<jti>`.
    - If `used` exists → invalidate all sessions, error “Token reuse detected”.
    - If stored JTI mismatch or missing → invalidate all sessions, error “Token invalidated or expired”.
    - Otherwise: delete active refresh key, set `used:<user_id>:<jti>` with TTL `used_ttl_seconds`, and return `OK`.
    - `used_ttl_seconds = min(REFRESH_TOKEN_USED_TTL_SECONDS, REFRESH_TOKEN_EXPIRE_MINUTES * 60)`; configured via `.env` (default 14 days).
  - On success, a new refresh token is issued with the same `session_id` and new `jti`; Redis stores `refresh:<user_id>:<session_id>` with TTL `REFRESH_TOKEN_EXPIRE_MINUTES`.

## 5) New access token
- The use case decodes the new refresh token to read the current `session_id` and calls `create_access_token` with that `session_id`.
- The use case returns both new tokens (`TokenModel`); the router then passes them through `TokenCookieResponder.apply` with the resolved transport, which either writes the refresh cookie and strips it from the body (`cookie`) or leaves the body untouched (`body`).

## 6) Invalidation helpers
- `invalidate_all_user_sessions` deletes `access:*`, `refresh:*`, `used:*` keys for the user; used when reuse/invalid structure is detected or when rotation fails the invariants.
