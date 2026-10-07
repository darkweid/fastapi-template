#!/usr/bin/env bash
# The Docker APT key ships in the repository instead of being fetched on the server;
# this pins it to the fingerprint Docker publishes.
set -euo pipefail

cd "$(dirname "$0")/.."
expected=9DC858229FC7DD38854AE2D88D81803C0EBFCD88
actual="$(gpg --show-keys --with-colons roles/docker/files/docker.asc | awk -F: '$1 == "fpr" { print $10; exit }')"
if [ "$actual" != "$expected" ]; then
    echo "docker.asc has fingerprint ${actual:-none}, expected $expected" >&2
    exit 1
fi
echo "docker.asc: $actual"
