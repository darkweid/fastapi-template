from pathlib import Path
import re
from urllib.parse import urlparse

# Keys carrying credentials. A value here that still equals the one in
# .env.example is a secret published in the repository. Anchored at the end on
# purpose: an unanchored "TOKEN" or "PASSWORD" also matches
# ACCESS_TOKEN_EXPIRE_MINUTES and RESET_PASSWORD_TOKEN_EXPIRE_MINUTES, whose
# values are supposed to equal the example.
SECRET_KEY_PATTERN = re.compile(r"(PASSWORD|SECRET|TOKEN|DSN|KEY|KEY_ID)$")

# Placeholder suffix .env.example uses, checked separately so an edited
# .env.example cannot quietly turn the comparison above into a no-op.
PLACEHOLDER_MARKER = "-not-real"

# Every spelling pydantic accepts, not just the obvious ones: DEBUG=on parses
# as True and would otherwise walk past a check that only knows "true".
TRUTHY_VALUES = {"1", "true", "t", "y", "yes", "on"}
FALSY_VALUES = {"0", "false", "f", "n", "no", "off"}

# Settings whose example value is fine locally but never in a deploy, mapped to
# the boolean that is forbidden there.
FORBIDDEN_BOOLEANS = {
    "DEBUG": True,
    "VALIDATE_CERTS": False,
    "COOKIE_SECURE": False,
}

# URLs that must point somewhere the outside world can reach. A deploy that
# keeps the example value here mails every user a link to their own machine.
PUBLIC_URL_KEYS = ("PUBLIC_BASE_URL",)

# get_s3_adapter refuses to build a client at all while S3 is disabled, so a
# placeholder credential sitting under this prefix costs nothing and should
# not fail the gate - only S3_ENABLED itself is checked in that state.
S3_KEY_PREFIX = "S3_"
# The literals below are hostnames to match, not addresses to bind to, which is
# what the linters read them as.
LOCAL_HOSTNAMES = {"localhost", "::1", "0.0.0.0"}  # noqa: S104 # nosec B104


def parse_env(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in path.read_text().strip().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        entries[key.strip()] = value.strip().strip('"').strip("'")
    return entries


def as_bool(value: str) -> bool | None:
    """Parses a value the way pydantic would, or None when it is not a boolean."""
    normalized = value.strip().lower()
    if normalized in TRUTHY_VALUES:
        return True
    if normalized in FALSY_VALUES:
        return False
    return None


def points_at_localhost(value: str) -> bool:
    hostname = urlparse(value.strip()).hostname
    if not hostname:
        return False
    hostname = hostname.lower()
    return hostname in LOCAL_HOSTNAMES or hostname.startswith("127.")


def collect_problems(example: dict[str, str], actual: dict[str, str]) -> list[str]:
    problems = []

    missing_keys = sorted(set(example) - set(actual))
    if missing_keys:
        problems.append(f"Missing keys in .env: {', '.join(missing_keys)}")

    s3_enabled = as_bool(actual.get("S3_ENABLED", "")) is True

    for key, value in sorted(actual.items()):
        skip_s3_placeholder = (
            key.startswith(S3_KEY_PREFIX) and key != "S3_ENABLED" and not s3_enabled
        )
        is_secret = bool(SECRET_KEY_PATTERN.search(key))
        copied_from_example = is_secret and value and value == example.get(key)
        if not skip_s3_placeholder and (
            copied_from_example or PLACEHOLDER_MARKER in value.lower()
        ):
            problems.append(
                f"{key} still holds the placeholder value from .env.example"
            )

        forbidden = FORBIDDEN_BOOLEANS.get(key)
        if forbidden is not None and as_bool(value) is forbidden:
            problems.append(f"{key}={value} is not allowed outside local development")

        if key in PUBLIC_URL_KEYS and points_at_localhost(value):
            problems.append(
                f"{key}={value} points at localhost: users receive email links "
                "they cannot open"
            )

    return problems


def check_env_file() -> None:
    """
    Checks that .env carries every key from .env.example and no placeholder values.
    """
    try:
        example = parse_env(Path(".env.example"))
        actual = parse_env(Path(".env"))
    except FileNotFoundError as e:
        print(f"File not found: {e}")
        raise SystemExit(1)

    problems = collect_problems(example, actual)
    if problems:
        for problem in problems:
            print(problem)
        raise SystemExit(1)

    print("All required keys are present in .env.")


if __name__ == "__main__":
    check_env_file()
