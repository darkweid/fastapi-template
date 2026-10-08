"""
Lua scripts for the cache.

Each script resolves every version counter a key composes from and touches the
value key in one round trip; a naive GET version + GET value would double the
latency of every cache read. A missing counter is a miss, and a write that finds
one missing starts a new generation from the server clock.
"""

from pathlib import Path

CACHE_GET_SCRIPT = (
    Path(__file__)
    .parent.joinpath("scripts", "cache_get.lua")
    .read_text(encoding="utf-8")
)
CACHE_SET_SCRIPT = (
    Path(__file__)
    .parent.joinpath("scripts", "cache_set.lua")
    .read_text(encoding="utf-8")
)
CACHE_DELETE_SCRIPT = (
    Path(__file__)
    .parent.joinpath("scripts", "cache_delete.lua")
    .read_text(encoding="utf-8")
)
CACHE_INVALIDATE_SCRIPT = (
    Path(__file__)
    .parent.joinpath("scripts", "cache_invalidate.lua")
    .read_text(encoding="utf-8")
)
