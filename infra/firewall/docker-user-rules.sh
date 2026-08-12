#!/usr/bin/env bash
# Filters traffic that Docker forwards to published container ports.
#
# Docker DNATs external packets straight into the FORWARD chain, so ufw never
# sees them and a published port is reachable from the internet even when ufw
# denies everything. Docker leaves the DOCKER-USER chain for exactly this case:
# it is evaluated before any Docker rule, so the policy below applies to every
# container regardless of which compose project published the port.
set -euo pipefail

PUBLIC_INTERFACE="${PUBLIC_INTERFACE:-$(ip route get 1.1.1.1 | awk '{print $5; exit}')}"
PUBLIC_TCP_PORTS="${PUBLIC_TCP_PORTS:-80,443}"

if [ -z "$PUBLIC_INTERFACE" ]; then
    echo "Cannot detect the public interface, set PUBLIC_INTERFACE explicitly" >&2
    exit 1
fi

apply_rules() {
    local command="$1"

    "$command" -F DOCKER-USER
    # Replies to connections opened by the containers themselves.
    "$command" -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
    # The only container ports the internet is allowed to reach.
    "$command" -A DOCKER-USER -i "$PUBLIC_INTERFACE" -p tcp \
        -m multiport --dports "$PUBLIC_TCP_PORTS" -j RETURN
    # Everything else arriving from the internet never reaches a container.
    "$command" -A DOCKER-USER -i "$PUBLIC_INTERFACE" -j DROP
    # Container-to-container and host-to-container traffic falls through.
    "$command" -A DOCKER-USER -j RETURN
}

iptables -N DOCKER-USER 2>/dev/null || true
apply_rules iptables
echo "DOCKER-USER configured on ${PUBLIC_INTERFACE}, open tcp: ${PUBLIC_TCP_PORTS}"

# Docker only creates the IPv6 chain when it manages ip6tables. IPv6 published
# ports are served by docker-proxy otherwise, and ufw already covers those.
if ip6tables -S DOCKER-USER >/dev/null 2>&1; then
    apply_rules ip6tables
    echo "DOCKER-USER (IPv6) configured on ${PUBLIC_INTERFACE}"
fi
