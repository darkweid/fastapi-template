"""
Lua sources for the steps that must not interleave. Rotation reads the old
refresh key, stamps the used marker and settles on a verdict as one operation;
consuming a single-use challenge compares its value and deletes it as one.
Spread over round-trips, two concurrent callers both come back valid.

Importing this module only reads the files - Redis parses a script on its first
EVAL, so a syntax error here surfaces on the first call, never at startup.
"""

from pathlib import Path

ROTATE_REFRESH_TOKEN_SCRIPT = (
    Path(__file__).with_name("rotate_refresh_token.lua").read_text(encoding="utf-8")
)

CONSUME_CHALLENGE_SCRIPT = (
    Path(__file__).with_name("consume_challenge.lua").read_text(encoding="utf-8")
)
