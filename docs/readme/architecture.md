# Architecture and Structure

## Core Architectural Patterns

### Unit of Work (UoW) Pattern
`src/core/database/uow.py` keeps DB work transactional and coordinates repositories.

- Transaction management: groups multiple DB operations to succeed or fail together.
- Repository coordination: single transaction boundary for multiple repositories.
- Clean API design: consistent interface (`commit`, `rollback`) for callers.

Implementations:
- `AbstractUnitOfWork`: contract.
- `SQLAlchemyUnitOfWork`: AsyncSession-based implementation.
- `ApplicationUnitOfWork`: app-specific factory wiring repositories.

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
│   │   ├── Dockerfile-postgis           # Dockerfile for PostgreSQL with PostGIS
│   │   ├── init-postgis.sh              # Initialization script
│   │   └── postgresql.conf              # PostgreSQL configuration
│   ├── redis.conf                       # Redis configuration
│   ├── requirements/                    # Python dependencies for different environments
│   │   ├── base.txt                     # Base dependencies used in all environments
│   │   ├── dev.txt                      # Development environment dependencies
│   │   └── prod.txt                     # Production environment dependencies
│   └── requirements.txt                 # Main requirements file
│
├── migrations/                          # Alembic migrations for database schema management
│   ├── versions/                        # Migration version files
│   ├── env.py                           # Alembic environment configuration
│   ├── script.py.mako                   # Alembic migration script template
│   └── README                           # Instructions for migrations
│
├── scripts/                             # Utility scripts for the application
│   ├── __init__.py                      # Package initialization
│   └── check_env.py                     # Environment validation script
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
│   │   └── routers.py                   # System API endpoints (health, time)
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
│   ├── auth/                            # Auth tests
│   ├── core/                            # Core tests
│   ├── email/                           # Email tests
│   ├── main/                            # Main module tests
│   ├── storage/                         # Storage adapter tests
│   └── system/                          # System routes tests
│
├── celery_tasks/                        # Celery task management
├── loggers/                             # Logging configurations
├── models/                              # Shared data models and models package initialization
├── Makefile                             # Makefile with predefined commands
├── alembic.ini                          # Alembic configuration file
├── pytest.ini                           # PyTest configuration
├── mypy.ini                             # MyPy configuration
├── README.md                            # Project documentation
└── pyproject.toml                       # Project and tooling configuration
```
