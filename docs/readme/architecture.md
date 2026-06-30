# Architecture and Structure

## Core Architectural Patterns

### Unit of Work (UoW) Pattern
The `src/core/database/uow/` package keeps DB work transactional and coordinates repositories.

- Transaction management: groups multiple DB operations to succeed or fail together.
- Repository coordination: single transaction boundary for multiple repositories.
- Clean API design: consistent interface (`commit`, `rollback`) for callers.

Implementations:
- `UnitOfWork`: abstract contract (`uow/abstract.py`).
- `SQLAlchemyUnitOfWork`: AsyncSession-based implementation (`uow/sqlalchemy.py`).
- `ApplicationUnitOfWork`: app-specific factory wiring repositories (`uow/application.py`).

### Main Module Architecture
`src/main/` wires the app and isolates bootstrapping concerns.
- `config.py`: Pydantic settings for DB, Redis, RabbitMQ, JWT, etc. `.env` is used by default; `.env.test` is used when `TESTING=true`. `SENTRY_ENABLED` gates Sentry even if DSN is set; Sentry is skipped in DEBUG/TESTING.
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

### Repository Access
- All DB work goes through repositories; no direct SQL in usecases/services/routers.
- Prefer base repository methods (e.g., `get_single`) before adding custom queries; if the same filters/settings are reused 2–3 times or more, extract them into a custom repository method.
- Keep repositories focused on data access; put orchestration and business logic in usecases/services.

### Advisory Transaction Locks
Use PostgreSQL advisory transaction locks to serialize critical sections without row-level locking.

- **Where:** `BaseRepository.xact_lock` / `BaseRepository.try_xact_lock`.
- **How it works:** locks are held only for the current transaction; they release automatically on commit/rollback.
- **When to use `xact_lock`:** when you must block until the lock is acquired (e.g., prevent duplicate workflow execution).
- **When to use `try_xact_lock`:** when you want a non-blocking check and a boolean result (e.g., skip work if already running).
- **Keying:** pass a string key; the repository namespaces it by model (`<table>:<key>`) before hashing to a 64-bit advisory lock key.
---
## Project Layout
```
├── infra/                               # Infrastructure and deployment assets
│   ├── docker/                          # Docker configuration files
│   │   ├── Dockerfile                   # Production Dockerfile (multi-stage build)
│   │   └── Dockerfile.dev               # Development Dockerfile with hot-reload
│   ├── docker-compose.override.yml      # Docker Compose overrides for development
│   ├── docker-compose.yml               # Docker Compose configuration
│   ├── nginx/                           # Nginx configuration
│   │   ├── app.conf                     # App reverse-proxy config
│   │   ├── main.conf                    # Shared proxy settings (upgrade headers, etc.)
│   │   └── dev-nginx.conf               # Dev-only reverse-proxy config
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
│   │   ├── database/                    # Database connection and ORM setup
│   │   ├── email_service/               # Email service functionality
│   │   ├── errors/                      # Error handling
│   │   ├── limiter/                     # Rate limiting functionality
│   │   ├── patterns/                    # Design patterns
│   │   ├── redis/                       # Redis caching system + limiter init
│   │   ├── storage/                     # Storage adapters (S3)
│   │   ├── utils/                       # Utility functions
│   │   ├── middleware.py                # Application middleware setup
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
│   ├── system/                          # System-level functionality
│   │   ├── dependencies.py              # System DI providers
│   │   ├── routers.py                   # System API endpoints (health, time)
│   │   ├── schemas.py                   # System Pydantic schemas
│   │   └── services.py                  # Health check service
│   │
│   └── user/                            # User functionality
│       ├── auth/                        # Authentication logic for regular users
│       ├── dependencies.py              # User dependencies
│       ├── models.py                    # User data models (ORM)
│       ├── repositories.py              # User data repository layer
│       ├── routers.py                   # User API endpoints
│       ├── schemas.py                   # User Pydantic schemas
│       ├── services.py                  # User business logic services
│       ├── tasks.py                     # Celery tasks for users
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
│       ├── celery_tasks/                # Celery task tests
│       └── src/                         # Mirrors src/ layout
│           ├── core/                    # Core component tests
│           ├── main/                    # Main module tests
│           ├── system/                  # System routes tests
│           └── user/                    # User & auth tests
│
├── celery_tasks/                        # Celery worker config and task management
├── loggers/                             # Logging configurations
├── models/                              # Shared data models and models package initialization
├── Makefile                             # Makefile with predefined commands
├── alembic.ini                          # Alembic configuration file
├── pytest.ini                           # PyTest configuration
├── mypy.ini                             # MyPy configuration
├── README.md                            # Project documentation
└── pyproject.toml                       # Project and tooling configuration
```
