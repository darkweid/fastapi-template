from ipaddress import ip_address, ip_network
import json
from pathlib import Path
from typing import Any

import yaml

from scripts.ops.check_env import parse_env
from src.main.config import AppConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _compose() -> dict[str, Any]:
    text = (PROJECT_ROOT / "infra/docker-compose.yml").read_text(encoding="utf-8")
    loaded: dict[str, Any] = yaml.safe_load(text)
    return loaded


def _app_network_ipam() -> dict[str, str]:
    configs = _compose()["networks"]["app-network"]["ipam"]["config"]
    assert len(configs) == 1
    ipam: dict[str, str] = configs[0]
    return ipam


def test_app_network_hands_out_addresses_outside_its_gateway() -> None:
    """Docker forwards the connections it proxies to a published port from the
    gateway, every IPv6 client included; the range containers draw from must
    not hold it, or trusting nginx's range would trust those clients too."""
    ipam = _app_network_ipam()
    subnet = ip_network(ipam["subnet"])
    address_range = ip_network(ipam["ip_range"])
    gateway = ip_address(ipam["gateway"])

    assert address_range.subnet_of(subnet)  # type: ignore[arg-type]
    assert gateway in subnet
    assert gateway not in address_range


def test_trust_proxy_hosts_default_is_loopback_and_the_app_network_range() -> None:
    """A wider default (every private network) held the gateway, so any IPv6
    client could forge X-Forwarded-For and pick its own rate-limit bucket."""
    default = AppConfig.model_fields["TRUST_PROXY_HOSTS"].default

    assert default == ["127.0.0.1", "::1", _app_network_ipam()["ip_range"]]


def test_env_example_trusts_the_same_proxy_hosts_as_the_default() -> None:
    example = parse_env(PROJECT_ROOT / ".env.example")

    assert (
        json.loads(example["TRUST_PROXY_HOSTS"])
        == AppConfig.model_fields["TRUST_PROXY_HOSTS"].default
    )


def test_redis_gets_its_own_password_and_no_other_secret() -> None:
    """The app's env_file carries every secret of the stack - JWT keys, SMTP,
    S3 - and Redis needs one of them. A process holding the rest is one more
    place for them to leak from."""
    redis = _compose()["services"]["redis"]

    assert "env_file" not in redis
    assert redis["environment"] == {"REDISCLI_AUTH": "${REDIS_PASSWORD}"}
    assert " -a " not in " ".join(redis["healthcheck"]["test"])
