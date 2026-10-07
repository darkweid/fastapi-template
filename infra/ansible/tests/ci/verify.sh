#!/usr/bin/env bash
# Checks the converged runner against the spec. Runs as the runner user; root-only
# reads go through sudo. Each role adds its own section.
set -euo pipefail

keys="${RUNNER_TEMP:?}/ansible-ci"
port=22022

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

echo "== sshd"
ops_config="$(sudo sshd -T -C user=ops,host=localhost,addr=127.0.0.1)"
deploy_config="$(sudo sshd -T -C user=deploy,host=localhost,addr=127.0.0.1)"
[ "$(grep '^port ' <<<"$ops_config")" = "port 22022" ] || fail "sshd does not listen on 22022 alone"
[ "$(grep '^allowusers ' <<<"$ops_config")" = $'allowusers ops\nallowusers deploy' ] || fail "AllowUsers is not exactly ops deploy"
for line in "permitrootlogin no" "passwordauthentication no" "kbdinteractiveauthentication no" \
    "x11forwarding no" "allowagentforwarding no" "allowtcpforwarding yes"; do
    grep -qx "$line" <<<"$ops_config" || fail "ops: expected '$line'"
done
for line in "allowtcpforwarding no" "permittty no"; do
    grep -qx "$line" <<<"$deploy_config" || fail "deploy: expected '$line'"
done
if ss -Hltn 'sport = :22' | grep -q .; then fail "something still listens on 22"; fi
sudo ufw show added | grep -q 'ufw allow 22022/tcp' || fail "22022/tcp is not allowed in ufw"
pty_output="$(ssh -tt -p "$port" -i "$keys/deploy" -o BatchMode=yes -o IdentitiesOnly=yes deploy@127.0.0.1 true 2>&1 || true)"
grep -q "PTY allocation request failed" <<<"$pty_output" || fail "deploy got a pty"
forward_output="$(timeout 20 ssh -p "$port" -i "$keys/deploy" -o BatchMode=yes -o IdentitiesOnly=yes \
    -W "127.0.0.1:$port" deploy@127.0.0.1 </dev/null 2>&1 || true)"
grep -q "administratively prohibited" <<<"$forward_output" || fail "deploy may open a forward"

echo "== docker"
for package in docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; do
    dpkg -s "$package" >/dev/null 2>&1 || fail "package $package is missing"
done
if dpkg -l 'moby-*' 2>/dev/null | grep -q '^ii'; then fail "moby packages are still installed"; fi
grep -q 'Signed-By: /etc/apt/keyrings/docker.asc' /etc/apt/sources.list.d/docker.sources || fail "the Docker source is not signed by the shipped key"
[ "$(sudo docker info --format '{{.LoggingDriver}}')" = "local" ] || fail "the log driver is not local"
[ "$(sudo docker info --format '{{.LiveRestoreEnabled}}')" = "true" ] || fail "live-restore is off"
sudo docker compose version >/dev/null || fail "docker compose is missing"

echo "OK"
