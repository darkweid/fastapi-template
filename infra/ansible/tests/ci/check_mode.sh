#!/usr/bin/env bash
# `make server-provision CHECK=1` previews a run, a port move included, and
# changes nothing on the server.
set -euo pipefail

cd "$(dirname "$0")/../.."
export ANSIBLE_CONFIG="$PWD/ansible.cfg"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

echo "== a preview of an unchanged server"
output="$(.venv/bin/ansible-playbook -i tests/ci/inventory playbooks/site.yml --check 2>&1)" || {
    printf '%s\n' "$output"
    fail "the preview of an unchanged server failed"
}
printf '%s\n' "$output" | sed -n '/^PLAY RECAP/,$p'
grep -qE 'changed=0 +unreachable=0 +failed=0' <<<"$(printf '%s\n' "$output" | sed -n '/^PLAY RECAP/,$p')" \
    || fail "the preview of an unchanged server reports changes"

echo "== a preview of a port move"
.venv/bin/ansible-playbook -i tests/ci/inventory playbooks/site.yml --check --diff \
    -e sshd_port=2222 -e bootstrap_port=22022 || fail "the preview of a port move failed"
ss -Hltn 'sport = :22022' | grep -q . || fail "the preview moved sshd off 22022"
if ss -Hltn 'sport = :2222' | grep -q .; then fail "the preview made sshd listen on 2222"; fi
if sudo ufw show added | grep -q 'ufw allow 2222/tcp'; then fail "the preview opened 2222 in ufw"; fi

echo "OK"
