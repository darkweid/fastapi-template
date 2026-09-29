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

CMD ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]
