# TEST GUIDE

This document is a single, universal testing standard for the template.
The goal is simple: write solid tests quickly and consistently, without tying the process to any specific domain.

## 1) Goal and boundaries

- Test behavior and contracts, not internal implementation details.
- One test - one scenario.
- Tests should be deterministic, isolated, and easy to read.
- Do not duplicate business logic inside tests.
- For unit and API integration tests in this template, avoid real external systems (network, Redis, S3, DB).

## 2) Required workflow

1. Read the target component and its dependencies.
2. Decide what kind of test you need:
- Unit (use case/service/helper)
- API/integration at FastAPI level (via test client)
- Contract/infrastructure (if the task specifically requires it)
3. Build a minimal scenario matrix:
- Happy path
- Unhappy path (validation errors, not found, forbidden, etc.)
- Edge cases (empty values, boundaries, repeated calls)
4. Prepare fixtures and test data (reuse existing ones).
5. Decide whether a docstring is needed using the docstring decision rule.
6. Verify side effects:
- dependency calls
- commit/rollback
- state changes
7. Run the relevant tests.
8. Make sure style and naming match project conventions.

## 3) Structure and naming

- Test paths should mirror the domain structure under `src/`.
- File names: `test_<feature>.py`.
- Test names: `test_<scenario>_<expected_result>`.
- For async tests, use `@pytest.mark.asyncio`.
- Do not pack too many scenarios into one test.

## 4) Test design rules

- Follow Arrange/Act/Assert (or given/when/then).
- `Docstring rule (GWT)`:
- Default: no docstring.
- A GWT docstring is required only when the test describes a business rule, a non-obvious scenario, or a critical unhappy-path with meaningful side effects.
- A GWT docstring is not allowed for trivial, purely technical, or self-explanatory tests.
- If a docstring only repeats the test name or lists mocks/internal wiring, remove it.
- `Docstring decision rule`:
- If test is domain rule OR non-obvious OR critical unhappy-path -> docstring required.
- Else -> no docstring.
- If a docstring is required, use this format:
```text
"""
Given: <precondition>
When: <action>
Then: <expected result>
"""
```
- Typical places where docstrings are often required: auth/user domain flows, business use cases/services, policy-heavy unhappy paths.
- Typical places where docstrings are usually not allowed: low-level `tests/core/*`, `tests/main/*`, `tests/storage/*`, `tests/email/*`, `tests/system/*`, and trivial mapping/wiring tests.
- Quality bar for GWT docstrings:
- The docstring must add information that is not already obvious from the test name.
- `Given` describes domain context, not mocks/internal setup.
- `When` describes one action, not a multi-step chain.
- `Then` describes an observable outcome (output, state, event), not internal calls.
- One test should have one failure reason; avoid overloaded `Then` blocks.
- Counter-example (bad, remove):
```text
Given: domain preconditions are set.
When: the action is executed.
Then: expected business outcome is returned.
```
- Counter-example (good, keep):
```text
Given: refresh token reuse marker already exists for this user.
When: refresh token rotation is executed with the same jti.
Then: all user sessions are invalidated and UnauthorizedException is raised.
```
- Assert only observable behavior:
- return value
- raised exception
- dependency calls
- storage/state changes
- In unit tests, mock external dependencies and I/O.
- In API tests, use the application test client and dependency overrides.
- Avoid brittle assertions (exact log text, incidental implementation details).
- It is fine to have multiple assertions in one assert block if they belong to the same scenario.

## 5) What to use from template test infrastructure

Core fixtures live in `tests/conftest.py`.

- `app` - FastAPI application
- `async_client` - HTTPX AsyncClient for `app`
- `dependency_overrides` - safe setup/reset for DI overrides
- `settings` - test `Config` from `get_settings` (used by app overrides)
- `fake_redis` - in-memory Redis
- `fake_s3` - in-memory S3
- `mock_mailer` / `email_service` - test mailer
- `fake_session` - fake async DB session
- `fake_uow` - fake UnitOfWork
- `app_with_fakes` - app with overridden dependencies
- `async_client_with_fakes` - client bound to `app_with_fakes`

Also available:
- `tests/fakes/` - in-memory/fake implementations
- `tests/factories/` - test data factories
- `tests/helpers/` - helpers for overrides, request building, limiter utilities

## 6) Minimal unit test template

```python
import pytest


@pytest.mark.asyncio
async def test_execute_with_valid_input_returns_expected_result(fake_uow):
    # Given
    usecase = ...
    fake_uow.some_repo.get_single.return_value = ...

    # When
    result = await usecase.execute(...)

    # Then
    assert result == ...
    fake_uow.commit.assert_awaited_once()
```

## 7) Minimal API test template

```python
import pytest


@pytest.mark.asyncio
async def test_get_resource_returns_200(async_client_with_fakes):
    # When
    response = await async_client_with_fakes.get("/api/v1/resource")

    # Then
    assert response.status_code == 200
    payload = response.json()
    assert "data" in payload
```

## 8) Quality checklist before finishing

- Happy path is covered.
- At least one error path is covered.
- Key side effects are verified.
- No unnecessary mocks or bloated setup.
- Tests are independent.
- No dependency on local machine state or secrets.
- Style and naming are consistent.
- Relevant test scope was executed locally.

## 9) Run commands

- Run all tests: `make test`
- Run one file: `TESTING=true python -m pytest tests/<path>/test_<name>.py -v`
- Run one test: `TESTING=true python -m pytest tests/<path>/test_<name>.py::test_<scenario> -v`
- Stop on first failure: `TESTING=true python -m pytest tests/<path> -x`
- Re-run only failed tests: `TESTING=true python -m pytest tests/<path> --lf`

## 10) Anti-patterns (do not do this)

- Testing private internals instead of the public contract.
- Making network calls in unit tests.
- Using uncontrolled randomness (flake-prone tests).
- Mixing multiple business scenarios in one test.

## 11) Expected output format for test work

When adding tests, always include:

1. A short list of scenarios you added.
2. A list of changed files.
3. Which test commands you ran and the result.
4. What is intentionally not covered yet (if anything).
