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
[ "$(systemctl is-enabled ssh.socket)" = "enabled" ] || fail "sshd is not socket-activated, so CI tests the wrong restart path"
if grep -q '^Port ' /etc/ssh/sshd_config; then fail "sshd_config still names a port of its own"; fi
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

echo "== firewall"
sudo ufw status | grep -q '^Status: active' || fail "ufw is not active"
sudo ufw status verbose | grep -q 'Default: deny (incoming), allow (outgoing)' || fail "ufw defaults are wrong"
for rule in 22022/tcp 80/tcp 443/tcp; do
    sudo ufw status | grep -qE "^${rule} +ALLOW" || fail "ufw does not allow $rule"
done
if sudo ufw status | grep -qE '^22/tcp '; then fail "22/tcp is still allowed"; fi
grep -qx 'IPV6=yes' /etc/default/ufw || fail "ufw leaves IPv6 unfiltered"
[ "$(systemctl is-enabled docker-user-firewall)" = "enabled" ] || fail "docker-user-firewall is not enabled"

expected_chain="$(printf '%s\n' \
    '-N DOCKER-USER' \
    '-A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN' \
    '-A DOCKER-USER -i docker0 -j RETURN' \
    '-A DOCKER-USER -i br-+ -j RETURN' \
    '-A DOCKER-USER -p tcp -m multiport --dports 80,443 -j RETURN' \
    '-A DOCKER-USER -j DROP' \
    '-A DOCKER-USER -j RETURN')"
check_chain() {
    local family actual
    for family in iptables ip6tables; do
        actual="$(sudo "$family" -S DOCKER-USER)"
        [ "$actual" = "$expected_chain" ] || fail "$family DOCKER-USER after $1 is:
$actual"
    done
}
check_chain "provisioning"
sudo systemctl restart docker-user-firewall
check_chain "a second apply"
sudo systemctl restart docker
check_chain "systemctl restart docker"
sudo pkill -9 -x dockerd
for _ in $(seq 1 30); do
    sudo docker info >/dev/null 2>&1 && break
    sleep 2
done
sudo docker info >/dev/null 2>&1 || fail "dockerd did not come back after a crash"
check_chain "a dockerd crash"

echo "== app_checkout"
sudo test -d /srv/app/.git || fail "/srv/app is not a clone"
[ "$(sudo stat -c '%U %a' /srv/app)" = "deploy 750" ] || fail "/srv/app is not deploy-owned 0750"
sudo -u deploy git -C /srv/app status --porcelain >/dev/null || fail "git refuses the checkout as deploy"
sudo -u deploy git -C /srv/app config core.sshCommand | grep -q id_ed25519_repo || fail "git fetch would not use the deploy key"
[ "$(sudo stat -c '%U %a' /home/deploy/.ssh/id_ed25519_repo)" = "deploy 600" ] || fail "the deploy key is not deploy-owned 0600"
sudo grep -q '^github.com ssh-ed25519 ' /home/deploy/.ssh/known_hosts || fail "GitHub's host key is not trusted for deploy"

echo "OK"
