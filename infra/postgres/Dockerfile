# The CI integration job runs a stock postgres service pinned to the same tag
# (.github/workflows/_ci.yml) - bump the two together, or CI tests one major
# version while every compose stack runs another.
FROM postgres:18-bookworm

# The server reads this file at every start (the CMD below), not only at initdb,
# so a changed setting reaches an existing database with the next image build and
# container recreation. Baked in rather than bind-mounted: a single-file bind mount
# keeps the inode it was created with, and a `git checkout` that replaces the file
# would leave the container reading the old one.
COPY --chmod=0644 infra/postgres/postgresql.conf /etc/postgresql/postgresql.conf
# postgresql.conf ends with `include_dir` on this directory, which must exist.
# A deploy's resource profile (its compose.yml passes RESOURCE_PROFILE) puts the
# sizing of infra/resources/<profile>/postgres there; without one it stays empty
# and the base sizing holds. A profile with no *.conf fails the build. Only that
# profile's postgres/ directory is mounted, so it alone joins the build cache
# key: a changed sizing file of this profile builds a new image, while an edit
# to another profile or to any compose.yml leaves the cached image, and the
# running server, alone. Without a profile the mount falls back to
# infra/postgres, which the COPY above already depends on, and nothing is copied.
ARG RESOURCE_PROFILE=
RUN --mount=type=bind,source=infra/${RESOURCE_PROFILE:+resources/$RESOURCE_PROFILE/}postgres,target=/tmp/profile \
    mkdir -p /etc/postgresql/conf.d \
    && if [ -n "$RESOURCE_PROFILE" ]; then \
        install -m 0644 /tmp/profile/*.conf /etc/postgresql/conf.d/; \
    fi

# ca-certificates: the slim base image ships none, and every pgBackRest call to
# the S3 endpoint fails with "unable to get local issuer certificate" without it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl pgbackrest \
    && rm -rf /var/lib/apt/lists/*

# Runs the backup schedule (infra/backup/crontab) in the sidecar container.
# The binary is checked against the SHA1 the release publishes.
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.49
ARG SUPERCRONIC_SHA1_AMD64=e63c11a9726b775a6a11801e81af4f3fb926aa68
ARG SUPERCRONIC_SHA1_ARM64=0b6c5bb743e0b0dafed1132198c81807927ac413
RUN case "$TARGETARCH" in \
        amd64) sha1="$SUPERCRONIC_SHA1_AMD64" ;; \
        arm64) sha1="$SUPERCRONIC_SHA1_ARM64" ;; \
        *) echo "unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /usr/local/bin/supercronic \
        "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}" \
    && echo "${sha1}  /usr/local/bin/supercronic" | sha1sum -c - \
    && chmod 0755 /usr/local/bin/supercronic

COPY --chmod=0644 infra/backup/pgbackrest.conf /etc/pgbackrest/pgbackrest.conf

CMD ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]
