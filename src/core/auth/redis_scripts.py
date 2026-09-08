"""
Redis Lua scripts for token management.

This module contains Lua scripts for atomic operations on tokens in Redis.
These scripts ensure that token operations are performed atomically,
preventing race conditions in token validation and rotation.
"""

from pathlib import Path

ROTATE_REFRESH_TOKEN_SCRIPT = (
    Path(__file__).with_name("rotate_refresh_token.lua").read_text(encoding="utf-8")
)
