# Refresh Token Rotation Flow

This document describes how refresh token rotation works step by step.

## 1) Token issuance (login)
- `LoginUserUseCase` creates a new `session_id` and `family` for the user.
- `create_access_token` issues an access token with `mode=access_token`, `jti`, `session_id` and stores `jti` in Redis under `access:<user_id>:<session_id>` with TTL = `ACCESS_TOKEN_EXPIRE_MINUTES`.
- `create_refresh_token` issues a refresh token with `mode=refresh_token`, `jti`, `session_id`, `family` and stores `jti` under `refresh:<user_id>:<session_id>` with TTL = `REFRESH_TOKEN_EXPIRE_MINUTES`; it also stores a family marker `family:<user_id>:<family>` with the same TTL.

## 2) Incoming refresh request
- Endpoint `POST /v1/users/auth/login/refresh` uses `get_access_by_refresh_token` to decode the provided refresh token.
- `verify_jti`:
  - Strips `Bearer` prefix if present and decodes JWT with `JWT_USER_SECRET_KEY`.
  - Extracts `jti`, `mode`, `sub` (user_id), `session_id`, optionally `family`.
  - For refresh tokens:
    - Checks `used:<user_id>:<jti>`; if it exists, all user sessions are invalidated and 401 is returned (“Token reuse detected”).
    - Checks that `family:<user_id>:<family>` exists; otherwise invalidates all sessions and fails.
  - Verifies the active JTI in Redis: key `<mode_without_suffix>:<user_id>:<session_id>` must equal the token JTI; otherwise 401 (“Token invalidated or expired”).
- Dependency returns `(user, payload)` to the use case.

## 3) Domain checks before rotation
- `GetTokensByRefreshUserUseCase` receives the current user from dependency.
  - If blocked → `PermissionDeniedException`.
  - If not verified → `InstanceProcessingException`.

## 4) Rotation execution
- `rotate_refresh_token`:
  - `validate_token_structure` ensures `sub`, `session_id`, `jti`, `family` are present; on failure invalidates all sessions.
  - `validate_token_family` ensures `family:<user_id>:<family>` exists; on failure invalidates all sessions.
  - `execute_token_rotation` runs a Lua script with keys `refresh:<user_id>:<session_id>` and `used:<user_id>:<jti>`.
    - If `used` exists → invalidate all sessions, error “Token reuse detected”.
    - If stored JTI mismatch or missing → invalidate all sessions, error “Token invalidated or expired”.
    - Otherwise: delete active refresh key, set `used:<user_id>:<jti>` with TTL `used_ttl_seconds`, and return `OK`.
    - `used_ttl_seconds = min(REFRESH_TOKEN_USED_TTL_SECONDS, REFRESH_TOKEN_EXPIRE_MINUTES * 60)`; configured via `.env` (default 14 days).
  - On success, a new refresh token is issued with the **same family**, new `session_id` and `jti`; Redis stores `refresh:<user_id>:<new_session_id>` with TTL `REFRESH_TOKEN_EXPIRE_MINUTES` and extends `family:<user_id>:<family>` TTL to the same window.

## 5) New access token
- The use case decodes the new refresh token to read the new `session_id` and calls `create_access_token` with that `session_id`.
- Response returns both new tokens (`TokenModel`).

## 6) Invalidation helpers
- `invalidate_all_user_sessions` deletes `access:*`, `refresh:*`, `family:*`, `used:*` keys for the user; used when reuse/invalid family/invalid structure is detected or when rotation fails the invariants.
