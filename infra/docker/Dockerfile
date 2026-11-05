# --- Build stage ---
FROM python:3.13-slim-bookworm AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUSERBASE=/install

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libpq-dev \
    make \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY infra/requirements/prod.txt infra/requirements/base.txt ./requirements/
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --user -r requirements/prod.txt

# --- Final stage ---
FROM python:3.13-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUSERBASE=/install \
    PATH="/install/bin:$PATH" \
    PYTHONPATH="/install/lib/python3.13/site-packages"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    wget \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && groupadd --system appuser \
    && useradd --system --gid appuser --create-home --home-dir /home/appuser appuser \
    && mkdir -p /data \
    && chown appuser:appuser /app /data

COPY --from=builder --chown=appuser:appuser /install /install
COPY --chown=appuser:appuser . .

# The base image ships the standard library without bytecode, `.dockerignore`
# keeps the project's out of the context and PYTHONDONTWRITEBYTECODE stops the
# runtime from caching any, so every process would compile both on each start.
RUN python -m compileall -q -j 0 /app /usr/local/lib/python3.13

USER appuser
