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
for expected in "ssh-ed25519 " "read-only deploy key" "SSH_KNOWN_HOSTS"; do
    grep -q "$expected" <<<"$output" || {
        echo "FAIL: the output lacks '$expected'" >&2
        exit 1
    }
done
