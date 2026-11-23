import re

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

# Every pattern here ends with \Z: `$` also matches before a trailing newline.

# Latin letters; single spaces, hyphens or apostrophes between parts.
# Length (2-30) is enforced by the schemas via Field, not here.
# Example: "Anne-Marie", "O'Brien", "John Smith"
FULL_NAME_PATTERN = re.compile(r"^[a-zA-Z]+(?:[ '\-][a-zA-Z]+)*\Z")

USERNAME_VALIDATOR = re.compile(r"^[a-zA-Z0-9_\-.]{4,60}\Z")

# Validates a phone number in international E.164 format (starts with + followed by country code and digits)
# Example: "+12025550179"
PHONE_NUMBER_PATTERN = re.compile(r"^\+[1-9]\d{1,14}\Z")

# Validates a strong password with at least one lowercase letter, one uppercase letter,
# one digit, one non-alphanumeric non-space character, and printable ASCII characters only.
# Example: "Passw0rd~"
STRONG_PASSWORD_VALIDATOR = re.compile(
    rf"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9\s])[ -~]{{{PASSWORD_MIN_LENGTH},{PASSWORD_MAX_LENGTH}}}\Z"
)


def validate_full_name(value: str) -> str:
    if not FULL_NAME_PATTERN.match(value):
        raise ValueError(
            "Name must contain latin letters, with single spaces, "
            "hyphens or apostrophes between parts"
        )
    return value


def validate_username(value: str) -> str:
    if not USERNAME_VALIDATOR.match(value):
        raise ValueError(
            "Username must be from 4 to 60 symbols and contain alphanumeric characters, underscore, dash, and dot"
        )
    return value
