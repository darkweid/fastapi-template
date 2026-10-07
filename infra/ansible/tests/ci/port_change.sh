#!/usr/bin/env bash
# Moving sshd on a provisioned box, the way README.md describes it. A new port the
# operator cannot reach must leave the old one working; a move that succeeds must
# close the old port in ufw.
set -euo pipefail

cd "$(dirname "$0")/../.."
export ANSIBLE_CONFIG="$PWD/ansible.cfg"
keys="${RUNNER_TEMP:?}/ansible-ci"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

provision() {
    .venv/bin/ansible-playbook -i tests/ci/inventory playbooks/site.yml "$@"
}

listening() {
    ss -Hltn "sport = :$1" | grep -q .
}

ops_login() {
    ssh -p "$1" -i "$keys/ops" -o BatchMode=yes -o IdentitiesOnly=yes ops@127.0.0.1 true
}

ufw_allows() {
    sudo ufw show added | grep -q "ufw allow $1/tcp"
}

echo "== a new port the operator cannot reach"
sudo iptables -I INPUT -p tcp --dport 2223 -j REJECT
if provision -e sshd_port=2223 -e bootstrap_port=22022; then
    fail "the run passed although port 2223 is unreachable"
fi
sudo iptables -D INPUT -p tcp --dport 2223 -j REJECT
listening 22022 || fail "sshd no longer listens on the old port"
ops_login 22022 || fail "ops can no longer log in on the old port"

echo "== the next run on the old port settles it"
provision
listening 22022 || fail "sshd does not listen on 22022"
if listening 2223; then fail "sshd still listens on 2223"; fi
if ufw_allows 2223; then fail "2223/tcp is still allowed in ufw"; fi

echo "== a port another service already holds"
python3 -m http.server 2224 --bind 0.0.0.0 >/dev/null 2>&1 &
holder=$!
for _ in $(seq 1 20); do
    listening 2224 && break
    sleep 0.5
done
if provision -e sshd_port=2224 -e bootstrap_port=22022; then
    kill "$holder"
    fail "the run moved sshd onto a port another service holds"
fi
kill "$holder"
[ "$(sudo sshd -T -C user=ops,host=localhost,addr=127.0.0.1 | grep '^port ')" = "port 22022" ] \
    || fail "the refused move still changed sshd's ports"
ops_login 22022 || fail "ops can no longer log in on 22022"

echo "== a move that succeeds"
provision -e sshd_port=2222 -e bootstrap_port=22022
listening 2222 || fail "sshd does not listen on 2222"
if listening 22022; then fail "sshd still listens on 22022"; fi
ops_login 2222 || fail "ops cannot log in on 2222"
ufw_allows 2222 || fail "2222/tcp is not allowed in ufw"
if ufw_allows 22022; then fail "22022/tcp is still allowed after the move"; fi

echo "== and back"
provision -e sshd_port=22022 -e bootstrap_port=2222
listening 22022 || fail "sshd does not listen on 22022"
if ufw_allows 2222; then fail "2222/tcp is still allowed after moving back"; fi

echo "OK"
