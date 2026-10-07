#!/usr/bin/env bash
# A repository the box cannot read yet: the run must print the deploy key and the
# summary, and still fail, so nobody mistakes it for a finished box.
set -euo pipefail

cd "$(dirname "$0")/../.."
export ANSIBLE_CONFIG="$PWD/ansible.cfg"

if output="$(.venv/bin/ansible-playbook -i tests/ci/inventory playbooks/site.yml \
    -e app_checkout_dir=/srv/ci-unreadable \
    -e app_checkout_repo_url=git@github.com:octocat/this-repository-does-not-exist.git 2>&1)"; then
    printf '%s\n' "$output"
    echo "FAIL: the run passed without access to the repository" >&2
    exit 1
fi
printf '%s\n' "$output"
for expected in "ssh-ed25519 " "read-only deploy key" "[127.0.0.1]:22022 ssh-ed25519 "; do
    grep -qF "$expected" <<<"$output" || {
        echo "FAIL: the output lacks '$expected'" >&2
        exit 1
    }
done
# .env goes in after the clone: git refuses to clone into a directory that holds it.
if grep -q ".env is missing" <<<"$output"; then
    echo "FAIL: the summary asks for .env before the repository is cloned" >&2
    exit 1
fi
