from pathlib import Path
import re

# Keys carrying credentials. A value here that still equals the one in
# .env.example is a secret published in the repository.
SECRET_KEY_PATTERN = re.compile(r"PASSWORD|SECRET|_KEY$|TOKEN|DSN")

# Placeholder suffix .env.example uses, checked separately so an edited
# .env.example cannot quietly turn the comparison above into a no-op.
PLACEHOLDER_MARKER = "-not-real"

# Settings whose example value is fine locally but never in a deploy.
FORBIDDEN_VALUES = {
    "DEBUG": {"true", "1", "yes"},
    "VALIDATE_CERTS": {"false", "0", "no"},
    "COOKIE_SECURE": {"false", "0", "no"},
}


def parse_env(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in path.read_text().strip().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        entries[key.strip()] = value.strip().strip('"').strip("'")
    return entries


def collect_problems(example: dict[str, str], actual: dict[str, str]) -> list[str]:
    problems = []

    missing_keys = sorted(set(example) - set(actual))
    if missing_keys:
        problems.append(f"Missing keys in .env: {', '.join(missing_keys)}")

    for key, value in sorted(actual.items()):
        is_secret = bool(SECRET_KEY_PATTERN.search(key))
        copied_from_example = is_secret and value and value == example.get(key)
        if copied_from_example or PLACEHOLDER_MARKER in value.lower():
            problems.append(
                f"{key} still holds the placeholder value from .env.example"
            )
        if value.lower() in FORBIDDEN_VALUES.get(key, set()):
            problems.append(f"{key}={value} is not allowed outside local development")

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
