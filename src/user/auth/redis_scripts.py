"""
Redis Lua scripts for token management.

This module contains Lua scripts for atomic operations on tokens in Redis.
These scripts ensure that token operations are performed atomically,
preventing race conditions in token validation and rotation.
"""

# Script for atomically checking and rotating a refresh token
# This prevents race conditions when multiple requests try to use the same token
ROTATE_REFRESH_TOKEN_SCRIPT = """
local refresh_key = KEYS[1]
local used_key = KEYS[2]
local expected_jti = ARGV[1]
local used_ttl_seconds = ARGV[2]

-- Check if the token has already been used
if redis.call('EXISTS', used_key) == 1 then
    return 'REUSED'
end

-- Atomically check and remove the active token
local stored_jti = redis.call('GET', refresh_key)
if stored_jti ~= expected_jti then
    return 'INVALID'
end

-- Mark as used and remove the active token
redis.call('SETEX', used_key, used_ttl_seconds, 'used')
redis.call('DEL', refresh_key)

return 'OK'
"""
