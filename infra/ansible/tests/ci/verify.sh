#!/usr/bin/env bash
# Checks the converged runner against the spec. Runs as the runner user; root-only
# reads go through sudo. Each role adds its own section.
set -euo pipefail

keys="${RUNNER_TEMP:?}/ansible-ci"
port=22

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

ssh_as() {
    local user="$1" key="$2"
    shift 2
    ssh -p "$port" -i "$keys/$key" -o BatchMode=yes -o IdentitiesOnly=yes "$user@127.0.0.1" "$@"
}

echo "== users"
ssh_as ops ops true || fail "ops cannot log in with the first key"
ssh_as ops ops2 true || fail "ops cannot log in with the second key"
[ "$(ssh_as ops ops sudo -n id -u)" = "0" ] || fail "ops cannot become root"
id -nG deploy | tr ' ' '\n' | grep -qx docker || fail "deploy is not in the docker group"
sudo -l -U deploy | grep -q "not allowed" || fail "deploy may use sudo"
[ ! -e "$HOME/.ssh/authorized_keys" ] || fail "the bootstrap user's SSH keys are still there"

echo "OK"
