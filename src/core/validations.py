import re

# Length constants
# Minimum length required for a valid password
# Example: Must have at least 8 characters
PASSWORD_MIN_LENGTH = 8
# Minimum length required for a valid phone number
# Example: Must have at least 5 digits
PHONE_NUMBER_MIN_LENGTH = 5

# Name and personal identifiers
# Validates a full name with alphanumeric characters only (no spaces)
# Example: "JohnDoe123"
FULL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9]{2,30}$")

# Validates a username with alphanumeric characters, underscore, dash, and dot
# Example: "john.doe_2023"
USERNAME_VALIDATOR = re.compile(r"^[a-zA-Z0-9_\-.]{4,60}$")

# Validates a name with letters and spaces only
# Example: "John Smith"
NAME_WITH_SPACES = re.compile(r"^[a-zA-Z\s]{2,50}$")

# Phone related
# Validates a phone number in international E.164 format (starts with + followed by country code and digits)
# Example: "+12025550179"
PHONE_NUMBER_PATTERN = re.compile(r"^\+[1-9]\d{1,14}$")

# Validates a phone country calling code
# Example: "+1", "+44"
PHONE_CODE_VALIDATOR = re.compile(r"^\+\d{1,5}$")

# Validates a phone number with optional leading +
# Example: "+12025550179" or "12025550179"
PHONE_NUMBER_REGEX = re.compile(r"^\+?\d{5,20}$")

# Text content
# Validates text containing only Latin alphabet, numbers, spaces, and basic punctuation
# Example: "Hello, world! This is a sample text."
LATIN_ALPHANUMERIC_REGEX = re.compile(r'^[A-Za-z0-9\s.,!?\'":;()\-_/\\]*$')

# Validates a URL slug in kebab-case format
# Example: "blog-post-title"
SLUG_VALIDATOR = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Web related
# Validates an email address
# Example: "user.name@example.com"
EMAIL_VALIDATOR = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9\-.]+$")

# Security
# Validates a strong password with at least one lowercase letter, one uppercase letter,
# one digit, one special character, and a minimum length of 8 characters
# Example: "Passw0rd!"
STRONG_PASSWORD_VALIDATOR = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$"
)

# Validates a JWT token format (three base64url-encoded segments separated by periods)
JWT_VALIDATOR = re.compile(r"^[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*$")

# Social
# Validates a Twitter/X handle, starting with @ followed by alphanumeric characters and underscores
# Example: "@username_123"
TWITTER_HANDLE_VALIDATOR = re.compile(r"^@[A-Za-z0-9_]{1,15}$")

# Validates an Instagram handle, starting with @ followed by alphanumeric characters, underscores, and dots
# Example: "@user.name_123"
INSTAGRAM_HANDLE_VALIDATOR = re.compile(r"^@[A-Za-z0-9_.]{1,30}$")
