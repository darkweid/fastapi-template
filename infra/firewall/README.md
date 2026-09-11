# Host firewall

Only `22`, `80` and `443` are reachable from the internet on a deployed host -
Nginx is the only service the compose file publishes on a public address.
Everything else - Postgres and Redis (published on `127.0.0.1` only), the worker
and scheduler, the app port, anything a neighbouring compose project publishes -
is reachable through an SSH tunnel only.

## Apply

```bash
scp -r infra/firewall <host>:/tmp/firewall
ssh <host> 'sudo bash /tmp/firewall/harden-host.sh'
```

`harden-host.sh` is idempotent and installs a `docker-user-firewall.service`
unit, so the Docker part of the policy survives reboots and daemon restarts.

Both scripts read their settings from the environment: `SSH_PORT` for a non-default
SSH port, `PUBLIC_TCP_PORTS` (default `80,443`) for the container ports the internet
may reach, and `PUBLIC_INTERFACE` when the public interface cannot be detected.

## Why two layers

`ufw` filters the INPUT chain, but Docker DNATs published ports into FORWARD
before ufw is consulted - a container published on `0.0.0.0` stays reachable
with ufw fully enabled. The `DOCKER-USER` chain runs before every Docker rule
and is the supported place to filter that traffic, so the two scripts split
along the same line: `ufw` for host listeners, `DOCKER-USER` for containers.
`docs/readme/security.md` explains the bypass in more detail.

This is a second line of defence, not the first one: the compose file publishes
nothing but Nginx on a public address and binds Postgres and Redis to
`127.0.0.1`, which the network cannot reach regardless of firewall state.

## Reaching an internal service

```bash
ssh -L 5432:127.0.0.1:5432 <host>    # Postgres
ssh -L 6379:127.0.0.1:6379 <host>    # Redis
```

The compose file publishes both on the host's loopback (`POSTGRES_PORT`,
`REDIS_PORT`), so a database client tunnelling to `127.0.0.1` on the host - an
IDE's SSH tunnel included - reaches a deployed stack the same way it reaches a
local one.
