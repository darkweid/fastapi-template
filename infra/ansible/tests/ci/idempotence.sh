#!/usr/bin/env bash
# site.yml again: anything it changes on a converged server is a role that is not
# idempotent.
set -euo pipefail

cd "$(dirname "$0")/../.."
export ANSIBLE_CONFIG="$PWD/ansible.cfg"

output="$(.venv/bin/ansible-playbook -i tests/ci/inventory playbooks/site.yml 2>&1)" || {
    printf '%s\n' "$output"
    exit 1
}
printf '%s\n' "$output"

recap="$(printf '%s\n' "$output" | sed -n '/^PLAY RECAP/,$p')"
if ! grep -qE 'changed=0 +unreachable=0 +failed=0' <<<"$recap"; then
    echo "The second run changed something" >&2
    exit 1
fi
