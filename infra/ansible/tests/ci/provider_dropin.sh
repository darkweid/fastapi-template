#!/usr/bin/env bash
# A provider file that adds itself to AllowUsers must fail the run instead of
# leaving the provider's account admitted next to ops and deploy.
set -euo pipefail

cd "$(dirname "$0")/../.."
export ANSIBLE_CONFIG="$PWD/ansible.cfg"

planted=/etc/ssh/sshd_config.d/10-provider.conf
trap 'sudo rm -f "$planted"' EXIT
echo "AllowUsers ubuntu" | sudo tee "$planted" >/dev/null

if output="$(.venv/bin/ansible-playbook -i tests/ci/inventory playbooks/site.yml 2>&1)"; then
    printf '%s\n' "$output"
    echo "FAIL: the run accepted a provider drop-in that adds a user" >&2
    exit 1
fi
printf '%s\n' "$output"
grep -q "overrides or adds to it" <<<"$output" || {
    echo "FAIL: the run failed for another reason" >&2
    exit 1
}
