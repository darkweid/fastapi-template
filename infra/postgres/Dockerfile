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

CMD ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]
