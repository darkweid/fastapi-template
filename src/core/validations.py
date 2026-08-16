import re

# Length constants
# Minimum length required for a valid password
# Example: Must have at least 8 characters
PASSWORD_MIN_LENGTH = 8
# Maximum length allowed for a valid password
# Example: Passwords longer than 128 characters are rejected
PASSWORD_MAX_LENGTH = 128

# Name and personal identifiers
# Latin letters; single spaces, hyphens or apostrophes between parts.
# Length (2-30) is enforced by the schemas via Field, not here.
# Example: "Anne-Marie", "O'Brien", "John Smith"
FULL_NAME_PATTERN = re.compile(r"^[a-zA-Z]+(?:[ '\-][a-zA-Z]+)*$")

# Validates a username with alphanumeric characters, underscore, dash, and dot
# Example: "john.doe_2023"
USERNAME_VALIDATOR = re.compile(r"^[a-zA-Z0-9_\-.]{4,60}$")

# Phone related
# Validates a phone number in international E.164 format (starts with + followed by country code and digits)
# Example: "+12025550179"
PHONE_NUMBER_PATTERN = re.compile(r"^\+[1-9]\d{1,14}$")

# Security
# Validates a strong password with at least one lowercase letter, one uppercase letter,
# one digit, one non-alphanumeric non-space character, and printable ASCII characters only.
# Example: "Passw0rd~"
STRONG_PASSWORD_VALIDATOR = re.compile(
    rf"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9\s])[ -~]{{{PASSWORD_MIN_LENGTH},{PASSWORD_MAX_LENGTH}}}$"
)
