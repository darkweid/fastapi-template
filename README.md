# FastAPI Template

This repository provides a template for FastAPI-based projects. It includes a structured directory layout, `Dockerfile`, `docker-compose.yml`, `.env` support, and essential dependencies.

## 📂 Project Structure

```
📦 fastapi_template
├── 📂 app              # Application source code
│   ├── 📂 core         # Core configurations and utilities
│   ├── 📂 admin        # Admin-related functionality
│   ├── 📂 cart         # Shopping cart module
│   ├── 📂 chain        # Chain-related logic
│   ├── 📂 logging      # Logging configuration
│   ├── 📂 news         # News management
│   ├── 📂 order        # Order processing
│   ├── 📂 payment      # Payment handling
│   ├── 📂 product      # Product management
│   ├── 📂 translate    # Translation module
│   ├── 📂 user         # User authentication and management
│   ├── 📂 vehicle      # Vehicle-related operations
│   ├── 📂 websocket    # WebSocket-based communication
├── 📂 celery_tasks     # Celery task definitions
├── 📂 loggers          # Logging configurations
├── 📂 logs             # Log storage
├── 📂 migrations       # Database migrations
├── 📂 scripts          # Utility scripts
├── 📂 templates        # Jinja2 templates
├── 📂 tests            # Test suite
├── 📜 Dockerfile       # Docker image definition
├── 📜 docker-compose.yml # Docker Compose configuration
├── 📜 .env             # Environment variables
├── 📜 .env.example     # Example environment variables
├── 📜 requirements.txt # Dependencies
├── 📜 README.md        # Documentation
├── 📜 Makefile         # Makefile for automation
└── 📜 main.py          # Entry point
```

## 🚀 Installation & Running

### 1. Clone the Repository
```sh
git clone https://github.com/darkweid/FastAPI_template.git
cd FastAPI_template
```

### 2. Install Dependencies
#### Locally (via `venv`)
```sh
python -m venv venv
source venv/bin/activate  # For Linux/macOS
venv\Scripts\activate  # For Windows
pip install -r requirements.txt
```

#### Using Docker
```sh
docker build -t fastapi-template .
```

### 3. Run the Application
#### Locally
```sh
uvicorn app.main:app --reload
```

#### Using Docker Compose
```sh
docker-compose up --build
```

## 📌 Key Dependencies
The `requirements.txt` file already includes essential dependencies such as:
- `fastapi`
- `uvicorn`
- `gunicorn`
- `pydantic`
- `sqlalchemy`
- `celery`
- `asyncpg`
- `pyjwt`
- `alembic`


---
🔧 Ready for customization and further development!

