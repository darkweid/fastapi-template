FROM postgres:18-bookworm

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        postgis \
        postgresql-18-postgis-3 \
        postgresql-18-postgis-3-scripts && \
    rm -rf /var/lib/apt/lists/*

# Copy custom configuration and initialization scripts into the image
COPY infra/postgres/postgresql.conf /custom-config/postgresql.conf
COPY infra/postgres/setup-config.sh /docker-entrypoint-initdb.d/00-setup-config.sh
COPY infra/postgres/init-postgis.sh /docker-entrypoint-initdb.d/init-postgis.sh

RUN chmod 0644 /custom-config/postgresql.conf \
    && chmod +x /docker-entrypoint-initdb.d/00-setup-config.sh \
    && chmod +x /docker-entrypoint-initdb.d/init-postgis.sh
