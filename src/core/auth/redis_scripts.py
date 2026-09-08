"""
Lua sources loaded at import time, so a syntax error in a script surfaces at
startup rather than on the first rotation. Rotation has to read the old key,
stamp the used marker and answer a verdict without another client interleaving
between those steps, which is why it is a script and not a pipeline.
"""

from pathlib import Path

ROTATE_REFRESH_TOKEN_SCRIPT = (
    Path(__file__).with_name("rotate_refresh_token.lua").read_text(encoding="utf-8")
)
