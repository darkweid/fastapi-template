#!/usr/bin/env bash
# bootstrap.yml on this runner: play 1 locally as the runner user, everything
# after it over real SSH as ops, starting on port 22.
set -euo pipefail

cd "$(dirname "$0")/../.."
export ANSIBLE_CONFIG="$PWD/ansible.cfg"

.venv/bin/ansible-playbook -i tests/ci/inventory playbooks/bootstrap.yml \
    -e bootstrap_user="$(id -un)" \
    -e bootstrap_connection=local \
    -e bootstrap_port=22 \
    -e bootstrap_full_upgrade=false
