"""
Lua sources for the steps that must not interleave. Rotation reads the old
refresh key, stamps the used marker and settles on a verdict as one operation;
spread over round-trips, two concurrent refreshes could both come back valid.

Importing this module only reads the files - Redis parses a script on its first
EVAL, so a syntax error here surfaces on the first rotation, never at startup.
"""

from pathlib import Path

ROTATE_REFRESH_TOKEN_SCRIPT = (
    Path(__file__).with_name("rotate_refresh_token.lua").read_text(encoding="utf-8")
)
