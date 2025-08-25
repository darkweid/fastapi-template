# FastAPI Template

A robust, production-ready FastAPI template designed to help you build scalable APIs using asynchronous endpoints, modular architecture, Docker, Celery for background tasks, and integrated monitoring with Flower and Nginx reverse-proxy.

> **Note:** This project uses a simplified Repository Pattern to abstract data access. Unlike the full Domain-Driven Design (DDD) approach—which typically involves complex aggregate roots and domain entities—the repository implementation here serves as a straightforward abstraction layer to decouple business logic from persistence concerns without the additional overhead of DDD.
>
> This template uses a modular structure with core components and domain-specific folders (e.g., `user` and `admin`). The `core` folder contains common infrastructure (database connections, settings, middleware, models, and schemas) while domain modules provide placeholders for business logic and API endpoints.

---

## Table of Contents

- [Features](#features)
- [Architecture & Directory Structure](#architecture--directory-structure)
- [Requirements](#requirements)
- [Containers](#containers)
- [Deployment & Setup](#deployment--setup)
- [Accessing the Application](#accessing-the-application)
- [Contributing](#contributing)
- [Acknowledgements](#acknowledgements)

---

## Features

- **FastAPI & Asynchronous Endpoints:** Leverage the performance and ease-of-use of FastAPI for building modern APIs.
- **Modular Architecture:** Organized codebase with a clear separation between core functionalities and domain-specific modules.
- **Database Integration:** Asynchronous PostgreSQL+PostGis connectivity using SQLAlchemy for async operations.
- **Celery for Background Tasks:** Background job processing powered by Celery with RabbitMQ as the broker and Redis as the backend.
- **Task Monitoring with Flower:** Monitor your Celery tasks in real time using Flower.
- **Docker & Docker Compose:** Containerized setup for consistent development, testing, and production deployments.
- **Nginx Reverse Proxy:** Configured to route external HTTP requests to your FastAPI application.
- **Pydantic Settings:** Centralized configuration management using Pydantic (via `pydantic-settings`).
- **Email Service:** Built-in email functionality with templating support.
- **Redis Caching:** Advanced caching system with Redis backend support.
- **Error Handling:** Comprehensive exception handling with custom exception types and handlers.
- **Rate Limiting:** Built-in rate limiting capabilities to protect your API endpoints.
- **Testing Framework:** Ready-to-use testing structure for unit and integration tests.

---

## Architecture & Directory Structure

```
├── docker/                              # Docker configuration files
│   ├── Dockerfile                       # Production Dockerfile (multi-stage build)
│   └── Dockerfile.dev                   # Development Dockerfile with hot-reload
│
├── migrations/                          # Alembic migrations for database schema management
│   ├── versions/                        # Migration version files
│   ├── env.py                           # Alembic environment configuration
│   ├── script.py.mako                   # Alembic migration script template
│   └── README                           # Instructions for migrations
│
├── postgres/                            # PostgreSQL configuration
│   ├── Dockerfile-postgis               # Dockerfile for PostgreSQL with PostGIS
│   ├── init-postgis.sh                  # Initialization script
│   └── postgresql.conf                  # PostgreSQL configuration
│
├── requirements/                        # Python dependencies for different environments
│   ├── base.txt                         # Base dependencies used in all environments
│   ├── dev.txt                          # Development environment dependencies
│   ├── test.txt                         # Testing environment dependencies
│   └── prod.txt                         # Production environment dependencies
│
├── scripts/                             # Utility scripts for the application
│   ├── __init__.py                      # Package initialization
│   └── check_env.py                     # Environment validation script
│
├── src/                                 # Application source code
│   ├── admin/                           # Admin functionality
│   │   ├── auth/                        # Authentication logic for admin users
│   │   ├── dependencies.py              # Admin dependencies
│   │   ├── exceptions.py                # Admin-specific exceptions
│   │   ├── models.py                    # Admin data models (ORM)
│   │   ├── repositories.py              # Admin data repository layer
│   │   ├── routers.py                   # Admin API endpoints
│   │   ├── schemas.py                   # Admin Pydantic schemas
│   │   └── services.py                  # Admin business logic services
│   │
│   ├── core/                            # Core components shared across the application
│   │   ├── database/                    # Database connection and ORM setup
│   │   │   ├── database_async.py        # Async database setup
│   │   │   ├── models.py                # Declarative Base and Mixins
│   │   │   ├── redis.py                 # Redis connection utilities
│   │   │   └── repositories.py          # Core data repositories
│   │   │
│   │   ├── email_service/               # Email service functionality
│   │   │   ├── config.py                # Email configuration
│   │   │   ├── dependencies.py          # Email dependencies
│   │   │   ├── fastapi_mailer.py        # FastAPI-Mail integration
│   │   │   ├── interfaces.py            # Email service interfaces
│   │   │   ├── schemas.py               # Email data schemas
│   │   │   ├── service.py               # Email service implementation
│   │   │   ├── tasks.py                 # Celery tasks for email
│   │   │   └── templates/               # Email templates
│   │   │
│   │   ├── errors/                      # Error handling
│   │   │   ├── exceptions.py            # Custom exception classes
│   │   │   └── handlers.py              # Exception handlers
│   │   │
│   │   ├── limiter/                     # Rate limiting functionality
│   │   │   ├── depends.py               # Dependencies for rate limiting
│   │   │   └── script.py                # Rate limiting implementation
│   │   │
│   │   ├── patterns/                    # Design patterns
│   │   │   └── singleton.py             # Singleton pattern implementation
│   │   │
│   │   ├── redis/                       # Redis caching system
│   │   │   ├── cache/                   # Caching implementation
│   │   │   │   ├── backend/             # Cache backends
│   │   │   │   ├── coder/               # Data encoding/decoding
│   │   │   │   ├── manager/             # Cache management
│   │   │   │   ├── decorators.py        # Cache decorators
│   │   │   │   ├── lifecycle.py         # Cache lifecycle management
│   │   │   │   └── tags.py              # Cache tagging system
│   │   │   ├── core.py                  # Redis core functionality
│   │   │   └── lifecycle.py             # Redis lifecycle management
│   │   │
│   │   ├── utils/                       # Utility functions
│   │   │   ├── datetime_utils.py        # Date and time utilities
│   │   │   ├── retry.py                 # Retry mechanism
│   │   │   └── security.py              # Security utilities
│   │   │
│   │   ├── middleware.py                # Application middleware setup
│   │   ├── routes.py                    # Core API routes
│   │   ├── schemas.py                   # Core data validation schemas
│   │   ├── services.py                  # Core services shared across modules
│   │   └── validations.py               # Data validation utilities
│   │
│   ├── main/                            # Application entry points
│   │   ├── config.py                    # Application configuration settings
│   │   ├── lifespan.py                  # Application lifecycle management
│   │   ├── presentation.py              # API presentation layer
│   │   └── web.py                       # FastAPI application setup
│   │
│   ├── system/                          # System-level functionality
│   │   └── routers.py                   # System API endpoints (health, time)
│   │
│   └── user/                            # User functionality
│       ├── auth/                        # Authentication logic for regular users
│       ├── dependencies.py              # User dependencies
│       ├── exceptions.py                # User-specific exceptions
│       ├── models.py                    # User data models (ORM)
│       ├── repositories.py              # User data repository layer
│       ├── routers.py                   # User API endpoints
│       ├── schemas.py                   # User Pydantic schemas
│       ├── services.py                  # User business logic services
│       └── tasks.py                     # Celery tasks for users
│
├── tests/                               # Test suite
│   └── email/                           # Tests for email functionality
│       ├── mocks.py                     # Mock objects for testing
│       └── test_email_service.py        # Tests for email service
│
├── celery_tasks/                        # Celery task management
│   └── main.py                          # Celery application setup
│
├── loggers/                             # Logging configurations
│   └── __init__.py                      # Logger setup
│
├── models/                              # Shared data models
│   └── __init__.py                      # Models package initialization
│
├── Makefile                             # Makefile with predefined commands
├── alembic.ini                          # Alembic configuration file
├── docker-compose.yml                   # Docker Compose configuration
├── docker-compose.override.yml          # Docker Compose overrides for development
├── nginx.conf                           # Nginx configuration
├── redis.conf                           # Redis configuration
├── requirements.txt                     # Main requirements file
├── pytest.ini                           # PyTest configuration
├── mypy.ini                             # MyPy configuration
└── main.py                              # Application entry point
```

---

## Requirements
- Docker & Docker Compose
---

## Containers
- **Postgres:**
  Hosts the PostgreSQL database with PostGIS capabilities. Built using `Dockerfile-postgis` in the postgres directory, it uses environment variables for credentials and persists data in a dedicated volume.

- **App:**
  Runs the main FastAPI application. It starts the server (using Uvicorn, or optionally Gunicorn with Uvicorn workers).

- **Celery_worker:**
  Executes background tasks using Celery.

- **Celery_beat:**
  Acts as the scheduler for periodic tasks.

- **Flower:**
  Provides real-time monitoring for Celery tasks. Based on the official Flower image, it’s built to include your project code.

- **Nginx:**
  Serves as a reverse proxy that routes external HTTP requests to the FastAPI application.

- **Redis:**
  Runs the Redis server for caching and as a Celery result backend. It is secured with a password and persists data in a Docker volume.

- **RabbitMQ:**
  Functions as the message broker for Celery tasks. It uses the official RabbitMQ image with the management plugin enabled, exposing both AMQP (for messaging) and management (for UI) ports.

---

## Deployment & Setup

This project is entirely containerized and must be deployed using Docker Compose. A Makefile is provided to simplify common tasks such as building, running, stopping, and managing migrations. Follow the instructions below to get started.

### Prerequisites

- **Docker:** [Install Docker](https://docs.docker.com/get-docker/)
- **Docker Compose:** [Install Docker Compose](https://docs.docker.com/compose/install/)

### Cloning the Repository

1. Open your terminal.
2. Clone the repository:

   ```bash
   git clone https://github.com/darkweid/fastapi-template.git
   cd fastapi-template
   ```

3. Copy the example environment file to create your own:

   ```bash
   cp .env.example .env
   ```

4. Open the `.env` file in your favorite editor and configure the required environment variables (e.g., database credentials, RabbitMQ settings, etc.).

### Running the Project

The project uses Docker Compose to orchestrate all services, including the FastAPI app, PostgreSQL, Redis, RabbitMQ, Celery Worker, Celery Beat, Flower, and Nginx. Use the provided Makefile to simplify the workflow.

#### Building & Starting the Containers

By following these instructions and using the provided Makefile, you can easily deploy and manage the entire project stack with a few simple commands. Happy coding!

To build the Docker images (if needed) and run all containers in detached mode, use:

```bash
make run
```

This command will:
- Build the images as necessary.
- Start all services defined in the `docker-compose.yml` file.

#### Running in Development Mode with Auto-Reload

For development with auto-reload functionality (hot reloading when code changes), use:

```bash
make run-dev
```

This command will:
- Build development images that include development dependencies like uvicorn.
- Start services with the configurations from both `docker-compose.yml` and `docker-compose.override.yml`.
- Enable auto-reload for the FastAPI application.

#### Viewing Logs

To view logs for all services:

```bash
make logs
```

To view logs for a specific service, such as the FastAPI app:

```bash
make logs-app
```

Other services (Celery Worker, Celery Beat, PostgreSQL, etc.) can be inspected using the corresponding Makefile targets (e.g., `logs-celery`, `logs-celery-beat`, `logs-postgres`).

#### Stopping and Cleaning Up

- To stop all running containers:

  ```bash
  make down
  ```

- To remove containers, networks, ***volumes***, and local images (and clean up orphaned containers):

  ```bash
  make clean
  ```

- To restart containers:

  ```bash
  make restart
  ```

#### Additional Commands

- **Open a Shell in the App Container:**

  ```bash
  make shell
  ```

- **Database Migrations:**

  - Create a new migration (replace `Your migration message` with an appropriate message):

    ```bash
    make migration message="Your migration message"
    ```

  - Apply all migrations:

    ```bash
    make migrate
    ```

#### Development and Production Deployment

For simplified deployment workflows, use:

- Development deployment:

  ```bash
  make deploy-dev
  ```

- Production deployment:

  ```bash
  make deploy-prod
  ```

#### Resource Cleanup

To clean up Docker resources:

- Basic cleanup (keeps build cache):

  ```bash
  make clean-resources
  ```

- Aggressive cleanup (removes all unused resources):

  ```bash
  make clean-resources-hard
  ```

---

### Accessing the Application

- **FastAPI Application:**
  The API is exposed via Nginx on port **80** (You can easily change port in `nginx.conf` and `docker-compose.yml` files). Open your browser and navigate to [http://localhost](http://localhost).

- **API Documentation:**
  Once the app is running, access Swagger UI at [http://localhost/docs](http://localhost/docs) or ReDoc at [http://localhost/redoc](http://localhost/redoc).

- **Flower Monitoring:**
  Monitor your Celery tasks at [http://localhost:5555](http://localhost:5555).

### Troubleshooting

- Ensure Docker and Docker Compose are properly installed.
- Verify that your `.env` file is correctly configured.
- Use `make logs` or specific log commands (e.g., `make logs-app`) to check for error messages.
- If you encounter issues with database migrations, make sure the PostgreSQL container is running and accessible.

---

## Contributing

Contributions are welcome! Please follow these steps:
1. Fork the repository.
2. Create a new branch (`git checkout -b feature/your-feature`).
3. Commit your changes.
4. Push to your branch.
5. Open a Pull Request with a clear description of your changes.

---

## Acknowledgements

- [FastAPI](https://fastapi.tiangolo.com/)
- [SQLAlchemy](https://www.sqlalchemy.org/)
- [Celery](https://docs.celeryproject.org/)
- [Flower](https://flower.readthedocs.io/)
- [Docker](https://www.docker.com/)
- [Pydantic](https://pydantic-docs.helpmanual.io/)
