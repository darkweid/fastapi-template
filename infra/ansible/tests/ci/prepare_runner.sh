#!/usr/bin/env bash
# Makes a GitHub-hosted runner look like a fresh box: no swap, sshd on 22, keys for
# ops and deploy, its own host keys trusted, and an authorized_keys of its own so
# the bootstrap user's lock is observable.
set -euo pipefail

if [ "${RUNNER_ENVIRONMENT:-}" != "github-hosted" ]; then
    echo "Refusing to prepare anything but a GitHub-hosted runner" >&2
    exit 1
fi

keys="${RUNNER_TEMP:?}/ansible-ci"
mkdir -p "$keys"

# The base role creates swap only on a box with none at all.
sudo swapoff -a
sudo sed -i '/\sswap\s/d' /etc/fstab
sudo rm -f /mnt/swapfile /swapfile

if ! dpkg -s openssh-server >/dev/null 2>&1; then
    sudo apt-get update -q
    sudo apt-get install -yq openssh-server
fi
sudo systemctl start ssh

for name in ops ops2 deploy; do
    ssh-keygen -q -t ed25519 -N '' -C "ci-$name" -f "$keys/$name"
done

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
cat "$keys/ops.pub" >> "$HOME/.ssh/authorized_keys"
# 22022 is the inventory's port; port_change.sh moves sshd to 2222 and 2223.
for host_key in /etc/ssh/ssh_host_*_key.pub; do
    read -r key_type key_body _ < "$host_key"
    for host in 127.0.0.1 '[127.0.0.1]:22022' '[127.0.0.1]:2222' '[127.0.0.1]:2223'; do
        printf '%s %s %s\n' "$host" "$key_type" "$key_body" >> "$HOME/.ssh/known_hosts"
    done
done
