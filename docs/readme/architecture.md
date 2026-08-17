# Architecture and Structure

## Core Architectural Patterns

### Unit of Work (UoW) Pattern
The `src/core/database/uow/` package keeps DB work transactional and coordinates repositories.

- Transaction management: groups multiple DB operations to succeed or fail together.
- Repository coordination: single transaction boundary for multiple repositories.
- Clean API design: consistent interface (`commit`, `rollback`) for callers.
- Exit contract: leaving the UoW context without an explicit `commit()` rolls the transaction back - commit is never implicit.

Implementations:
- `UnitOfWork`: abstract contract (`uow/abstract.py`).
- `SQLAlchemyUnitOfWork`: AsyncSession-based implementation (`uow/sqlalchemy.py`).
- `ApplicationUnitOfWork`: app-specific factory wiring repositories (`uow/application.py`).

### Main Module Architecture
`src/main/` wires the app and isolates bootstrapping concerns.
- `config.py`: Pydantic settings for DB, Redis, JWT, etc. `.env` is used by default; `.env.test` is used when `TESTING=true`. `SENTRY_ENABLED` gates Sentry even if DSN is set; Sentry is skipped in DEBUG/TESTING.
- `lifespan.py`: startup/shutdown lifecycle (init/cleanup external resources).
- `presentation.py`: API assembly, versioning, exception handlers.
- `route_logging.py`: logs routes grouped by method/tag for debugging.
- `web.py`: FastAPI app factory with middleware, CORS, Sentry, routers.

Benefits:
- Separation of concerns per file.
- Modularity and extendability.
- Centralized configuration and consistent error handling.
- Clear startup/shutdown ownership for resources.

### Core Components (extended)
- **Storage (S3):** Async adapter in `src/core/storage/s3` with presigned URLs, UploadFile support, and paginated listings. Use via DI (`src/core/storage/s3/dependencies.get_s3_adapter`).

### UseCase vs Service (Formalization)
**UseCase (Application Service)**
- Use when the operation is a scenario, not a single business rule.
- Always: controls the transaction (UoW), orchestrates steps, may touch multiple repositories/services, may call external ports (S3/Email/Payment/HTTP), is responsible for side effects (events/queues), and shapes the final DTO/response.
- Forbidden: heavy business logic inside; push domain rules into Services.

**Service (Domain / Module Service)**
- Encapsulates business logic of a single module.
- By default: uses only its own repository, no external systems, no cross-context knowledge, holds domain rules (validations/invariants/calculations).
- Exceptions: may use multiple repositories of the same bounded context if it stays a pure domain rule (not a scenario or I/O process).
- Size rule of thumb: if a method grows beyond ~30–40 LOC, has 3+ branches, or 3+ sequential steps, it’s turning into a scenario → move to a UseCase.

**External systems (S3, Email, Queues, HTTP clients)**
- Always at the UseCase level or in infra adapters used by a UseCase.

### Routers Never Receive a DB Session
Routers only inject the Service or UseCase they call; the session itself stops one layer earlier. No exceptions — the system probes get theirs the same way, through `get_readiness_service`, not through a session dependency on the route.
- `BaseService.__init__(repository, session, response_schema=None)` takes the session as a constructor argument; the DI provider that builds the service (e.g. `get_user_service`, `get_readiness_service`) resolves it via `Depends(get_session)` and passes it in. Every CRUD method then forwards `self.session` to the repository — no method still accepts a `session` argument.
- Anti-pattern: injecting `AsyncSession` into a router, or passing a session from a router into a service method.

### Repository Access
- All DB work goes through repositories; no direct SQL in usecases/services/routers.
- Prefer base repository methods (e.g., `get_single`) before adding custom queries; if the same filters/settings are reused 2–3 times or more, extract them into a custom repository method.
- Keep repositories focused on data access; put orchestration and business logic in usecases/services.

### List Queries (Search, Sort, Date Range)
A list endpoint's search, sort and date-range filtering is described declaratively with `ListQuery` (`src/core/database/query.py`), not hand-rolled per repository.

- **Repository side:** a repository opts client-selectable sorting in by declaring `searchable_fields`, `sortable_fields` and `default_order_by` as class attributes on `BaseRepository`. Both allowlists are empty by default — a client cannot search or pick a sort column until a domain explicitly opts it in, because `search` and `order_by` arrive from client input and must never reach an arbitrary column. Never put secrets, hashes or tokens in `searchable_fields`. This does not mean an undeclared repository has no ordering at all: when the client omits `order_by`, results are ordered by `default_order_by` if set, else by `created_at` if the model has that column, else only by the primary key.

```python
class ArticleRepository(SoftDeleteRepository[Article]):
    model = Article
    searchable_fields = ("title", "summary")
    sortable_fields = ("created_at", "title")
    default_order_by = "created_at"
```

- **Construction-time validation:** `BaseRepository.__init__` runs `_assert_list_query_fields()`, which checks that every declared `sortable_fields` / `default_order_by` attribute resolves to a real column (not a hybrid property or a relationship — neither is orderable, and both would otherwise pass and crash at query time instead) and that `searchable_fields` are text columns (`String`, not `Enum`). A misdeclared column raises when the repository is constructed, not when a request supplies a bad field — a developer-time error, not a client-triggered one.
- **HTTP side:** endpoints declare `Annotated[ListQueryParams, Query()]` (`src/core/pagination/schemas.py`) and translate it with `params.to_list_query(date_field=..., conditions=...)` into a `ListQuery`. Both `date_field` and `conditions` are chosen by the server — the client only supplies `search`, `order_by`, `order`, `date_from`, `date_to`. `ListQueryParams` inherits `extra="forbid"`, so an unlisted query parameter (an analytics tag, a cache-buster, an ad-hoc filter) is rejected with 422 rather than silently ignored; a route that needs extra filters subclasses `ListQueryParams` instead of declaring them alongside it.

```python
@router.get("/articles")
async def list_articles(
    params: Annotated[ListQueryParams, Query()],
    use_case: Annotated[ListArticlesUseCase, Depends(get_list_articles_use_case)],
) -> PaginatedResponse[ArticleViewModel]:
    return await use_case.execute(params)
```

The use case passes the resulting `ListQuery` straight to `get_paginated_list(session, page, size, query=list_query)`, which builds the `WHERE`/`ORDER BY` clauses and runs a matching `COUNT` for `total`.

- **Errors:** a field outside the allowlist, `date_from` after `date_to`, a period requested against a column the model doesn't have, or a `search` term against a repository with no `searchable_fields`, all raise `FilteringError`, mapped to HTTP 400.
- **Period bounds:** both are normalised to UTC before they are compared or bound, so a client may mix an offset-aware bound with a naive one; a naive value is read as UTC, matching the `DateTime(timezone=True)` columns every timestamp in this project uses. Bounds are inclusive on both ends.
- **Ordering:** `ListQuery.build_order_by` always appends the primary key after the requested sort column. Without that tiebreaker, limit/offset pagination over a non-unique sort column silently duplicates and skips rows between pages. The requested/default sort column is built with `nulls_last()`, so NULLs always sort last regardless of `asc`/`desc`, overriding PostgreSQL's own default — this matters because that column may be nullable. The primary-key tiebreaker clause deliberately omits `nulls_last()`: a primary key is `NOT NULL`, so the modifier would only force a `Sort` node instead of letting a plain btree index serve deep-offset pages.
- **Search performance:** `search` compiles to `ILIKE '%...%'`, which cannot use a plain btree index. On a large table, back it with `pg_trgm` (`CREATE EXTENSION pg_trgm` plus a GIN index with `gin_trgm_ops` on the searched columns) or full-text search.

### Advisory Transaction Locks
Use PostgreSQL advisory transaction locks to serialize critical sections without row-level locking.

- **Where:** `BaseRepository.xact_lock` / `BaseRepository.try_xact_lock`.
- **How it works:** locks are held only for the current transaction; they release automatically on commit/rollback.
- **When to use `xact_lock`:** when you must block until the lock is acquired (e.g., prevent duplicate workflow execution).
- **When to use `try_xact_lock`:** when you want a non-blocking check and a boolean result (e.g., skip work if already running).
- **Keying:** pass a string key; the repository namespaces it by model (`<table>:<key>`) before hashing to a 64-bit advisory lock key.

## Object-level authorization (ownership)

Role → permission (`require_permission`) answers "may this role use this
feature". It does not answer "may this caller touch THIS object" — the gap
behind BOLA, the top item of the OWASP API Top 10.

The reference guard is `require_self_or_permission(path_param, fallback)`
(`src/user/auth/permissions/ownership.py`), applied to `GET /v1/users/{user_id}`:
the caller passes when the id is their own, or when their role holds the
fallback permission. Failure answers **404**, never 403 — a foreign id must be
indistinguishable from a nonexistent one.

Porting the rule to a domain entity with an `owner_id` column: load the object
in the UseCase, compare the field against the caller, raise
`InstanceNotFoundException` on mismatch. Use the dependency form only when
ownership is visible right in the path. Every endpoint that takes a foreign
identifier must carry a test proving "foreign object → 404".

`src/note/` (`policies.ensure_note_access`, used by the update/delete
UseCases and by `GET /v1/notes/{note_id}`) is the worked example of the
`owner_id` variant: same 404-on-mismatch rule, an optional `has_permission`
escape hatch for roles that may reach another user's object.

---
## Project Layout
```
├── infra/                               # Infrastructure and deployment assets
│   ├── docker/                          # Docker configuration files
│   │   ├── Dockerfile                   # Production Dockerfile (multi-stage build)
│   │   └── Dockerfile.dev               # Development Dockerfile with hot-reload
│   ├── docker-compose.override.yml      # Docker Compose overrides for development
│   ├── docker-compose.test.yml          # Throwaway PostgreSQL for the integration suite
│   ├── docker-compose.yml               # Docker Compose configuration
│   ├── nginx/                           # Nginx configuration
│   │   ├── app.conf                     # App reverse-proxy server (http)
│   │   ├── main.conf                    # Top-level nginx settings
│   │   ├── proxy.inc                    # Shared proxy settings and JSON error pages
│   │   └── tls.conf.example             # TLS drop-in replacement for app.conf
│   ├── postgres/                        # PostgreSQL configuration
│   │   ├── Dockerfile                   # Dockerfile for PostgreSQL
│   │   └── postgresql.conf              # PostgreSQL configuration
│   ├── redis.conf                       # Redis configuration
│   ├── requirements/                    # Python deps (pip-tools: *.in sources → *.txt lockfiles)
│   │   ├── base.txt                     # Base dependencies used in all environments
│   │   ├── dev.txt                      # Development environment dependencies
│   │   ├── prod.txt                     # Production environment dependencies
│   │   └── security.txt                 # CI security tooling (bandit, pip-audit)
│   └── requirements.txt                 # Convenience wrapper (installs dev deps by default)
│
├── migrations/                          # Alembic migrations for database schema management
│   ├── versions/                        # Migration version files
│   ├── env.py                           # Alembic environment configuration
│   ├── script.py.mako                   # Alembic migration script template
│   └── README                           # Instructions for migrations
│
├── scripts/                             # Utility scripts for the application
│   ├── __init__.py                      # Package initialization
│   ├── check_env.py                     # Environment validation script
│   ├── sort_requirements_in.py          # Sort entries in requirements *.in files
│   └── sync_precommit_mypy_deps.py      # Sync mypy pre-commit deps with pinned requirements
│
├── src/                                 # Application source code
│   ├── core/                            # Core components shared across the application
│   │   ├── cache/                       # Cache protocol, key builders, route caching
│   │   ├── database/                    # Database connection, UoW and ORM setup
│   │   ├── email_service/               # Email service functionality
│   │   ├── errors/                      # Error handling
│   │   ├── limiter/                     # Rate limiting functionality
│   │   ├── outbox/                      # Transactional outbox for taskiq enqueue
│   │   ├── pagination/                  # PaginationParams and ListQueryParams
│   │   ├── patterns/                    # Design patterns
│   │   ├── redis/                       # Redis clients + limiter init
│   │   ├── storage/                     # Storage adapters (S3)
│   │   ├── utils/                       # Utility functions
│   │   ├── middleware.py                # Application middleware setup
│   │   ├── proxy_headers.py             # TrustedProxyHeadersMiddleware (X-Forwarded-* handling)
│   │   ├── request_context.py           # Request-id ContextVar shared by middleware and logging
│   │   ├── schemas.py                   # Core data validation schemas
│   │   ├── services.py                  # Core services shared across modules
│   │   └── validations.py               # Data validation utilities
│   │
│   ├── main/                            # Application entry points
│   │   ├── config.py                    # Application configuration settings
│   │   ├── lifespan.py                  # Application lifecycle management
│   │   ├── presentation.py              # API presentation layer
│   │   ├── route_logging.py             # Utility for logging routes summary
│   │   └── web.py                       # FastAPI application setup
│   │
│   ├── note/                            # Reference flat domain module - copy this to start a new one
│   │   ├── dependencies.py              # Note DI providers
│   │   ├── models.py                    # Note data model (ORM)
│   │   ├── policies.py                  # Pure ownership rule (ensure_note_access)
│   │   ├── repositories.py              # Note data repository layer
│   │   ├── routers.py                   # Note API endpoints
│   │   ├── schemas.py                   # Note Pydantic schemas
│   │   ├── services.py                  # Note business logic service
│   │   └── usecases/                    # Note-related use cases
│   │
│   ├── system/                          # System-level functionality
│   │   ├── dependencies.py              # System DI providers
│   │   ├── routers.py                   # System API endpoints (live, ready, health, time)
│   │   ├── schemas.py                   # System Pydantic schemas
│   │   └── services.py                  # Health check service
│   │
│   └── user/                            # Auth infrastructure (accounts, sessions, permissions)
│       ├── auth/                        # Authentication logic for regular users
│       ├── dependencies.py              # User dependencies
│       ├── models.py                    # User data models (ORM)
│       ├── repositories.py              # User data repository layer
│       ├── routers.py                   # User API endpoints
│       ├── schemas.py                   # User Pydantic schemas
│       ├── services.py                  # User business logic services
│       ├── tasks.py                     # taskiq tasks for users
│       └── usecases/                    # User-related use cases
│
├── tests/                               # Test suite
│   ├── conftest.py                      # Global fixtures (fakes, clients, settings)
│   ├── TEST_GUIDE.md                    # Testing standard for the template
│   ├── factories/                       # Test data factories
│   ├── fakes/                           # In-memory fakes for external systems
│   ├── helpers/                         # Test helpers and dependency overrides
│   └── unit/                            # Unit tests
│       ├── test_nginx_security_config.py # Nginx security config check
│       ├── taskiq_worker/               # taskiq worker tests
│       └── src/                         # Mirrors src/ layout
│           ├── core/                    # Core component tests
│           ├── main/                    # Main module tests
│           ├── note/                    # Note reference module tests
│           ├── system/                  # System routes tests
│           └── user/                    # User & auth tests
│
├── taskiq_worker/                       # taskiq broker, scheduler and worker DI
├── loggers/                             # Logging configurations
├── models/                              # Shared data models and models package initialization
├── Makefile                             # Makefile with predefined commands
├── alembic.ini                          # Alembic configuration file
├── pytest.ini                           # PyTest configuration
├── mypy.ini                             # MyPy configuration
├── README.md                            # Project documentation
└── pyproject.toml                       # Project and tooling configuration
```

## Adding a Domain Module

Copy `src/note/` as the starting point - it carries the full CRUD + ownership +
list-query pattern with no auth-specific baggage. Then wire the module in;
every step below lives outside the module itself and is easy to forget:

1. **Register the models** - add `from src.<module>.models import <Model> as <Model>`
   to `models/__init__.py`. **Skipping this fails silently:** Alembic's `env.py`
   imports the `models` package to discover tables, so an unregistered model
   produces an *empty* autogenerated migration with no error anywhere. A unit
   guard (`tests/unit/test_models_registration.py`) fails if a `src/**/models.py`
   module is missing here.
2. **Unit of Work** - add a cached property for the new repository on
   `ApplicationUnitOfWork` (`src/core/database/uow/application.py`).
3. **Router** - mount the module router under `/v1` in `src/main/presentation.py`.
4. **Permissions** - add `Permission` enum members and grants in
   `ROLE_PERMISSIONS` (`src/user/auth/permissions/`); protect sensitive
   endpoints with `RateLimiter`.
5. **Migration** - `make migration m="add <module>"` and review the diff before
   committing. An empty diff means step 1 was missed.
6. **Tests** - `tests/unit/src/<module>/` mirroring the source layout.
