# Adding an Auth Realm

An auth realm is everything that distinguishes one class of principal (users,
staff, a partner API) from another: its signing secrets, its Redis key
namespace, and its cookie names. `src/core/auth/` implements token issuing,
verification, rotation, cookies, CSRF, single-use challenges and the request
dependencies once, parametrized by an `AuthRealm` (`src/core/auth/realm.py`).
Everything under `src/user/auth/` is the *user* realm's declaration and
wiring - a template for a second realm, not code the second realm calls into.

## Part 1: Declaring a New Realm

Follow `src/user/auth/` as the worked example throughout.

1. **Declare the realm.** Create `src/<module>/auth/realm.py`:
   ```python
   STAFF_AUTH_REALM = AuthRealm(
       name="staff",
       session_secret=lambda: config.jwt.JWT_STAFF_SECRET_KEY,
       one_time_secrets={...},  # only the purposes this realm actually issues
       refresh_cookie_path="/v1/staff/auth/login/refresh",
   )
   ```
   The name becomes the Redis key prefix and the default cookie names
   (`<name>_refresh_token`, `<name>_csrf_token`). `refresh_cookie_path` must
   match where the realm's router ends up mounted (step 6) - a test should
   pin this the way `tests/unit/src/user/auth/test_token_transport.py` pins
   the user realm's.

2. **Add the secret to config.** One field per realm in `JWTConfig`
   (`src/main/config.py`), e.g. `JWT_STAFF_SECRET_KEY: str = Field(min_length=SECRET_MIN_LENGTH)`,
   plus one entry per single-use purpose the realm declares. Add the same
   variables to `.env.example` (with the `-not-real` placeholder marker) and
   `.env.test`. Nothing else in config changes: `JWTConfig.reject_shared_secrets`
   already walks every `*_SECRET_KEY` field by name, so a new field is
   automatically checked for collisions against every other realm's secrets -
   no update needed there. That validator compares config *field values*, not
   realm *declarations*: it cannot catch step 1's `session_secret` lambda
   copy-pasted to read `JWT_USER_SECRET_KEY` instead of the new field - that
   passes the validator cleanly (both fields hold different values, and
   nothing checks which realm reads which). Double-check the lambda names
   the realm's own field before moving on.

3. **Create the principal.** A model (`src/<module>/models.py`), a repository
   (`src/<module>/repositories.py`, declarative one-liner over `BaseRepository`),
   and a `policies.py` with the admission gate - the callable `build_realm_auth`
   will call to decide whether an authenticated principal may actually use the
   session (`src/user/policies.py`'s `ensure_can_use_session` is the reference:
   raise the real, unmasked reason, since the caller already holds a valid token).

4. **Wire the realm's dependencies.** `src/<module>/auth/dependencies.py`:
   ```python
   MODULE_AUTH = build_realm_auth(
       realm=STAFF_AUTH_REALM,
       principal_type=Staff,
       repository_factory=StaffRepository,
       admission=ensure_staff_can_use_session,
   )
   AuthenticatedStaff = Authenticated[Staff]
   get_token_cookie_responder = MODULE_AUTH.cookie_responder
   get_current_staff = MODULE_AUTH.current_principal
   # ... one alias per RealmAuth field the module's router needs, following
   # src/user/auth/dependencies.py's list verbatim.
   ```
   This file stays under thirty lines, same as the user realm's.

5. **Write the router.** Login and any realm-specific flows (register, email
   verification, password reset, ...) are the module's own scenarios - build
   them the same way `src/user/auth/usecases/login.py` does, using
   `issue_session_pair` and `LoginThrottle` from
   `src/core/auth/session_issuance.py`. Refresh and logout need no new
   UseCase at all: import `RefreshAccessUseCase` and `LogoutUseCase` from
   `src/core/auth/usecases/` and give each a thin provider module next to the
   realm's other usecases - `src/<module>/auth/usecases/refresh_access.py`
   and `src/<module>/auth/usecases/logout.py`, mirroring
   `src/user/auth/usecases/refresh_access.py` / `logout.py`. Each file is a
   single `get_*_use_case(redis_client: Annotated[Redis, Depends(get_redis_client)])`
   that builds the core UseCase with the realm, an admission callable and a
   `claims_builder`; the router only imports the provider and depends on it,
   the same way it depends on every other UseCase. Writing a new UseCase
   class here would be exactly the copy this factoring was meant to avoid.

6. **Mount the router.** Add it to `src/main/presentation.py` under `/v1`,
   then check that the mounted path matches the `refresh_cookie_path`
   declared in step 1 - a mismatch breaks browser refresh silently, because
   the refresh cookie is scoped to a path the router was never served at.

7. **Copy `permissions/`.** Duplicate `src/user/auth/permissions/` into the
   new module, replace `Permission`/the role enum and `ROLE_PERMISSIONS` with
   the new realm's, and repoint `checker.py`'s `get_current_user` import at
   the new realm's `get_current_<principal>` alias from step 4.

**`src/core/auth/` is not touched by any of this.** If adding a realm seems
to require a change there, the abstraction has leaked somewhere upstream of
this recipe - treat that as a separate change to propose and review on its
own, not a shortcut to fold into the new realm's commit.

## Part 2: An OTP Delivery Channel

A realm that authenticates by one-time code (SMS, email) instead of a
password reuses the same core, plus `ActiveChallengeRegistry`
(`src/core/auth/challenges.py`):

- **Never store the code itself.** `ActiveChallengeRegistry.consume`
  (`src/core/auth/challenges.py`) checks the presented value against the
  stored one with a plain equality comparison, so the stored value must be
  deterministic - Argon2 (`src/core/utils/security.py`'s password hasher) is
  salted and produces a different hash every call, which would make every
  check fail. Use a keyed digest instead: `hmac.new(realm.one_time_secret(purpose).encode(), code.encode(), hashlib.sha256).hexdigest()`,
  computed the same way to store and to compare. Keying the digest with the
  realm's own one-time secret means a Redis dump still doesn't hand a reader
  a table to brute-force the code against.
- **A correct code is spent by the check itself.** `consume` compares and
  deletes in one Redis operation, so exactly one of any number of concurrent
  presentations of one code gets through, and a successful check needs no
  follow-up `invalidate` - calling one retires whatever challenge is live by
  then, which may already be a newer one. The other side of that contract: a
  caller whose own work fails after a successful `consume` must issue a new
  code rather than expect the old one to work again.
- **Count attempts separately from the challenge.** A wrong value deliberately
  consumes nothing - the code stays live for its owner - so this class alone
  gives a short numeric code no brute-force resistance, and the counter is
  mandatory rather than optional for one. The challenge key holds the live
  code's digest; a second, realm-keyed counter tracks failed attempts against
  it, independent of the challenge's own TTL. Exhausting the counter should
  retire the challenge (`ActiveChallengeRegistry.invalidate`) rather than
  leaving it guessable until it expires on its own.
- **TTLs are minutes, not seconds** - an OTP a user has to fetch from a text
  message or an inbox needs more slack than an in-process token exchange.
- **Sending is a side effect, so it lives in a UseCase**, calling the SMS or
  email adapter directly - never from a Service. Generating the code, hashing
  it and calling `ActiveChallengeRegistry.store` all happen in the same
  UseCase, before the send, so a failed send never leaves a stored challenge
  the user was never told the value of.
