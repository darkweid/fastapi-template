#!/usr/bin/env bash
# Filters traffic Docker forwards to published container ports. Installed by
# infra/ansible/roles/firewall; settings come from /etc/default/docker-user-firewall.
#
# Docker DNATs published ports straight into FORWARD, so ufw never sees that
# traffic; DOCKER-USER runs before every Docker rule. The chain names no external
# interface: iptables accepts a name that does not exist, so after a rename an
# interface-bound rule would match nothing and leave every published port open.
# Whatever is not allowed here is dropped instead.
set -euo pipefail

PUBLIC_TCP_PORTS="${PUBLIC_TCP_PORTS:-80,443}"
TRUSTED_INTERFACES="${TRUSTED_INTERFACES:-}"

rules() {
    local interface
    echo "*filter"
    # Declaring the chain flushes it within the same restore, so the old and new
    # rule sets swap atomically.
    echo ":DOCKER-USER - [0:0]"
    # Replies to connections the containers opened.
    echo "-A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN"
    # Traffic from containers: the default bridge and every compose network.
    echo "-A DOCKER-USER -i docker0 -j RETURN"
    echo "-A DOCKER-USER -i br-+ -j RETURN"
    for interface in ${TRUSTED_INTERFACES//,/ }; do
        echo "-A DOCKER-USER -i ${interface} -j RETURN"
    done
    echo "-A DOCKER-USER -p tcp -m multiport --dports ${PUBLIC_TCP_PORTS} -j RETURN"
    echo "-A DOCKER-USER -j DROP"
    echo "COMMIT"
}

rules | iptables-restore --noflush
rules | ip6tables-restore --noflush
