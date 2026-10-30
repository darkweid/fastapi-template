from pathlib import Path
import re
import sys
from urllib.parse import urlparse

# Keys carrying credentials. A value here that still equals the one in
# .env.example is a secret published in the repository. Anchored at the end on
# purpose: an unanchored "TOKEN" or "PASSWORD" also matches
# ACCESS_TOKEN_EXPIRE_MINUTES and RESET_PASSWORD_TOKEN_EXPIRE_MINUTES, whose
# values are supposed to equal the example. PASS is pgBackRest's spelling
# (PGBACKREST_REPO1_CIPHER_PASS).
SECRET_KEY_PATTERN = re.compile(r"(PASSWORD|PASS|SECRET|TOKEN|DSN|KEY|KEY_ID)$")

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

# Blank in .env.example because a local stack runs without them, required on a
# deployed box. RESOURCE_PROFILE picks infra/resources/<profile>/; deploy.sh
# also refuses a name with no profile behind it.
REQUIRED_IN_DEPLOY_KEYS = ("RESOURCE_PROFILE",)

# get_s3_adapter refuses to build a client at all while S3 is disabled, so a
# placeholder credential sitting under this prefix costs nothing and should
# not fail the gate - only S3_ENABLED itself is checked in that state.
S3_KEY_PREFIX = "S3_"
# The literals below are hostnames to match, not addresses to bind to, which is
# what the linters read them as.
LOCAL_HOSTNAMES = {"localhost", "::1", "0.0.0.0"}  # noqa: S104 # nosec B104

ENV_FILE = Path(".env")
ENV_EXAMPLE_FILE = Path(".env.example")
# pgBackRest credentials, kept out of .env so the app, worker and scheduler
# never receive them: compose loads this file only into postgres and the backup
# sidecar. Read only while BACKUP_ENABLED is true.
BACKUP_ENV_FILE = Path(".env.backup")
BACKUP_ENV_EXAMPLE_FILE = Path(".env.backup.example")
BACKUP_FLAG_KEY = "BACKUP_ENABLED"
# Stays in .env: compose hands it to pgBackRest as archive-push-queue-max.
BACKUP_QUEUE_MAX_KEY = "BACKUP_ARCHIVE_QUEUE_MAX"
# The subset of pgBackRest 2.60's size syntax a person writes: digits, then an
# optional unit, case ignored, every unit binary (KB is KiB). pgBackRest refuses
# anything else at option parsing, so archive-push fails before its async process
# starts and nothing bounds the queue.
BACKUP_QUEUE_MAX_PATTERN = re.compile(
    r"^([0-9]+)(?:([kmgtp])(?:ib|b)?|b)?$", re.IGNORECASE
)
BACKUP_QUEUE_MAX_UNITS = {"k": 1, "m": 2, "g": 3, "t": 4, "p": 5}
# Below one WAL segment every segment is dropped; above 4PiB pgBackRest refuses.
BACKUP_QUEUE_MAX_RANGE = (16 * 1024**2, 4 * 1024**5)
BACKUP_FILE_REQUIRED_KEYS = (
    "PGBACKREST_REPO1_S3_BUCKET",
    "PGBACKREST_REPO1_S3_ENDPOINT",
    "PGBACKREST_REPO1_S3_REGION",
    "PGBACKREST_REPO1_S3_KEY",
    "PGBACKREST_REPO1_S3_KEY_SECRET",
    "PGBACKREST_REPO1_CIPHER_PASS",
)
# compose env_file, `docker run --env-file` and parse_value disagree on quotes,
# "$" interpolation and "#" comments, so a value carrying one of them would
# reach pgBackRest differently depending on who started it.
BACKUP_FORBIDDEN_CHARACTERS = "\"'$#"
BACKUP_ENDPOINT_KEY = "PGBACKREST_REPO1_S3_ENDPOINT"
BACKUP_CIPHER_PASS_KEY = "PGBACKREST_REPO1_CIPHER_PASS"  # nosec B105
# The runbook generates it with `openssl rand -hex 32`; a short or hand-typed
# passphrase is what encrypts every backup in the bucket.
BACKUP_CIPHER_PASS_PATTERN = re.compile(r"^[A-Za-z0-9]{32,}$")


# Compose's dotenv reader: a quoted value is what lies between its quotes and
# anything after the closing quote is ignored; an unquoted value ends at the
# first "#" that follows whitespace. Reading it any other way lets
# `DEBUG=true # temp` reach as_bool as "true # temp", parse as neither boolean,
# and pass the gate while the container runs with DEBUG on.
DOUBLE_QUOTED_VALUE = re.compile(r'"((?:[^"\\]|\\.)*)"')
SINGLE_QUOTED_VALUE = re.compile(r"'([^']*)'")
INLINE_COMMENT = re.compile(r"\s#")


def parse_value(raw: str) -> str:
    value = raw.strip()
    for quoted in (DOUBLE_QUOTED_VALUE, SINGLE_QUOTED_VALUE):
        if match := quoted.match(value):
            return match.group(1)
    # Split before stripping: in `KEY= # note` the whitespace ahead of "#" is
    # what makes it a comment.
    return INLINE_COMMENT.split(raw, maxsplit=1)[0].strip()


def parse_raw_env(path: Path) -> dict[str, str]:
    """Values exactly as written, quotes and inline comments included."""
    entries: dict[str, str] = {}
    for line in path.read_text().strip().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        entries[key.strip()] = value
    return entries


def parse_env(path: Path) -> dict[str, str]:
    return {key: parse_value(raw) for key, raw in parse_raw_env(path).items()}


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


def parse_queue_max(value: str) -> int | None:
    """Bytes in a pgBackRest size, or None when check_env does not accept it."""
    match = BACKUP_QUEUE_MAX_PATTERN.match(value)
    if match is None:
        return None
    number, unit = match.groups()
    size = int(number) * 1024 ** BACKUP_QUEUE_MAX_UNITS.get((unit or "").lower(), 0)
    low, high = BACKUP_QUEUE_MAX_RANGE
    return size if low <= size <= high else None


def is_dormant_s3_key(key: str, s3_enabled: bool) -> bool:
    """S3 keys other than the flag itself are optional while S3 is disabled."""
    return key.startswith(S3_KEY_PREFIX) and key != "S3_ENABLED" and not s3_enabled


def is_backup_enabled(env: dict[str, str]) -> bool:
    return as_bool(env.get(BACKUP_FLAG_KEY, "")) is True


def collect_backup_problems(
    example: dict[str, str], actual: dict[str, str] | None
) -> list[str]:
    """
    Checks raw .env.backup values (parse_raw_env) against .env.backup.example;
    `actual` is None when the file does not exist.
    """
    if actual is None:
        return [
            f"{BACKUP_ENV_FILE}: file not found next to .env, "
            f"{BACKUP_FLAG_KEY}=true needs it"
        ]

    problems = []
    missing_keys = sorted(set(example) - set(actual))
    if missing_keys:
        problems.append(f"{BACKUP_ENV_FILE}: missing keys: {', '.join(missing_keys)}")

    for key, raw in sorted(actual.items()):
        value = raw.strip()
        if any(character in value for character in BACKUP_FORBIDDEN_CHARACTERS):
            problems.append(
                f'{BACKUP_ENV_FILE}: {key} contains a quote, "$" or "#", '
                "which compose and docker read differently"
            )
            continue

        if key in BACKUP_FILE_REQUIRED_KEYS and not value:
            problems.append(f"{BACKUP_ENV_FILE}: {key} is empty")
            continue

        is_secret = bool(SECRET_KEY_PATTERN.search(key))
        if (is_secret and value == example.get(key)) or (
            PLACEHOLDER_MARKER in value.lower()
        ):
            problems.append(
                f"{BACKUP_ENV_FILE}: {key} still holds the placeholder value "
                f"from {BACKUP_ENV_EXAMPLE_FILE}"
            )
        elif key == BACKUP_ENDPOINT_KEY and "/" in value:
            # pgBackRest takes a bare host; a scheme or a path fails only at
            # stanza-create, after PostgreSQL already archives into the spool.
            problems.append(
                f"{BACKUP_ENV_FILE}: {key} must be a host without scheme or path "
                "(nbg1.your-objectstorage.com)"
            )
        elif key == BACKUP_CIPHER_PASS_KEY and not BACKUP_CIPHER_PASS_PATTERN.match(
            value
        ):
            problems.append(
                f"{BACKUP_ENV_FILE}: {key} must be at least 32 letters and digits "
                "(openssl rand -hex 32)"
            )

    return problems


def collect_problems(example: dict[str, str], actual: dict[str, str]) -> list[str]:
    problems = []

    s3_enabled = as_bool(actual.get("S3_ENABLED", "")) is True
    backup_enabled = is_backup_enabled(actual)

    missing_keys = sorted(
        key
        for key in set(example) - set(actual)
        if not is_dormant_s3_key(key, s3_enabled)
    )
    if missing_keys:
        problems.append(f"Missing keys in .env: {', '.join(missing_keys)}")

    for key, value in sorted(actual.items()):
        skip_s3_placeholder = is_dormant_s3_key(key, s3_enabled)
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

        if key in REQUIRED_IN_DEPLOY_KEYS and not value.strip():
            problems.append(f"{key} is empty: a deploy needs it set")

        if key == BACKUP_FLAG_KEY and not value.strip():
            problems.append(f"{key} is empty: set true or false")
        elif key == BACKUP_FLAG_KEY and as_bool(value) is None:
            problems.append(f"{key}={value} is not a boolean")

        if key == BACKUP_QUEUE_MAX_KEY and backup_enabled and not value.strip():
            problems.append(f"{key} is empty: {BACKUP_FLAG_KEY}=true needs it set")
        elif (
            key == BACKUP_QUEUE_MAX_KEY
            and backup_enabled
            and (parse_queue_max(value) is None)
        ):
            problems.append(
                f"{key}={value} is not a size pgBackRest accepts: a whole number "
                "with an optional unit (MiB, GiB, TiB; MB, GB, TB read the same), "
                "between 16MiB (one WAL segment) and 4PiB"
            )

        if key in PUBLIC_URL_KEYS and points_at_localhost(value):
            problems.append(
                f"{key}={value} points at localhost: users receive email links "
                "they cannot open"
            )

    return problems


def check_env_file() -> None:
    """
    Checks that .env carries every key from .env.example and no placeholder values,
    and with BACKUP_ENABLED true does the same for .env.backup.
    """
    try:
        example = parse_env(ENV_EXAMPLE_FILE)
        actual = parse_env(ENV_FILE)
        backup_example = (
            parse_raw_env(BACKUP_ENV_EXAMPLE_FILE)
            if is_backup_enabled(actual)
            else None
        )
    except FileNotFoundError as e:
        print(f"File not found: {e}")
        raise SystemExit(1) from e

    problems = collect_problems(example, actual)
    if backup_example is not None:
        backup_actual = (
            parse_raw_env(BACKUP_ENV_FILE) if BACKUP_ENV_FILE.exists() else None
        )
        problems.extend(collect_backup_problems(backup_example, backup_actual))
    if problems:
        for problem in problems:
            print(problem)
        raise SystemExit(1)

    print("All required keys are present in .env.")


def print_env_value(key: str) -> None:
    """Prints one value of .env as compose reads it, empty when the key is absent,
    so shell callers (deploy.sh, the Makefile) need no dotenv parser of their own."""
    print(read_env_value(key))


def print_env_flag(key: str) -> None:
    """Prints `true` or `false` for a boolean in .env, parsed the way pydantic
    and the gate parse it, so a shell caller never compares spellings itself.
    Exits 1 on anything else, an absent key included."""
    value = read_env_value(key)
    parsed = as_bool(value)
    if parsed is None:
        print(f"{key}={value} is not a boolean", file=sys.stderr)
        raise SystemExit(1)
    print("true" if parsed else "false")


def read_env_value(key: str) -> str:
    try:
        entries = parse_env(ENV_FILE)
    except FileNotFoundError as e:
        print(f"File not found: {e}", file=sys.stderr)
        raise SystemExit(1) from e
    return entries.get(key, "")


def main(argv: list[str]) -> None:
    if not argv:
        check_env_file()
    elif len(argv) == 2 and argv[0] == "--get":
        print_env_value(argv[1])
    elif len(argv) == 2 and argv[0] == "--enabled":
        print_env_flag(argv[1])
    else:
        print("usage: check_env.py [--get KEY | --enabled KEY]", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
