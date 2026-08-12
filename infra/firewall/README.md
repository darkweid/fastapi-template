# Host firewall

Only `22`, `80` and `443` are reachable from the internet on a deployed host -
which is exactly what the production compose file publishes (Nginx on `80`/`443`).
Everything else - Postgres, Redis, RabbitMQ, the app port, anything a neighbouring
compose project publishes - is reachable through an SSH tunnel only.

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

This is a second line of defence, not the first one: the production compose file
publishes nothing but Nginx, and the dev overlay binds the backing services to
`127.0.0.1`, which the network cannot reach regardless of firewall state.

## Reaching an internal service

```bash
ssh -L 5432:127.0.0.1:5432 <host>    # Postgres
ssh -L 15672:127.0.0.1:15672 <host>  # RabbitMQ management UI
```

Those host ports exist only when the dev overlay is in use; the production stack
keeps the backing services on the compose network, reachable from the host with
`docker compose exec`.
