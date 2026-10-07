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

echo "== base"
for package in acl ca-certificates curl git make python3 ufw unattended-upgrades; do
    dpkg -s "$package" >/dev/null 2>&1 || fail "package $package is missing"
done
[ "$(swapon --show=NAME --noheadings)" = "/swapfile" ] || fail "swap is not /swapfile"
[ "$(sudo stat -c %a /swapfile)" = "600" ] || fail "/swapfile is not 0600"
grep -q '^/swapfile none swap sw' /etc/fstab || fail "/swapfile is not in fstab"
[ "$(sysctl -n vm.swappiness)" = "10" ] || fail "vm.swappiness is not 10"
[ "$(timedatectl show --property=Timezone --value)" = "UTC" ] || fail "timezone is not UTC"
[ "$(timedatectl show --property=NTP --value)" = "yes" ] || fail "NTP is off"
grep -qx 'SystemMaxUse=500M' /etc/systemd/journald.conf.d/60-size.conf || fail "journald has no size cap"
grep -q 'Automatic-Reboot "false"' /etc/apt/apt.conf.d/52unattended-upgrades-local || fail "unattended-upgrades may reboot"
grep -q 'Unattended-Upgrade "1"' /etc/apt/apt.conf.d/20auto-upgrades || fail "unattended-upgrades is off"
if [ -d /etc/needrestart ]; then
    grep -q "restart} = 'l'" /etc/needrestart/conf.d/50-ansible.conf || fail "needrestart may restart services"
fi

echo "OK"
