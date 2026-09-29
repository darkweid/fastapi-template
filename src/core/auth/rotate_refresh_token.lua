local refresh_key = KEYS[1]
local used_key = KEYS[2]
local access_key = KEYS[3]
local sessions_key = KEYS[4]
local expected_jti = ARGV[1]
local used_ttl_seconds = ARGV[2]
local grace_seconds = tonumber(ARGV[3])
local session_id = ARGV[4]
local new_refresh_jti = ARGV[5]
local refresh_ttl_seconds = tonumber(ARGV[6])
local new_access_jti = ARGV[7]
local access_ttl_seconds = tonumber(ARGV[8])

-- The Redis server clock, not the app's: every app instance compares reuse
-- against the same clock, so the grace window needs no clock sync between them.
local now = tonumber(redis.call('TIME')[1])

-- A marker younger than the grace window is a benign double-submit (network
-- retry, two tabs racing); older, or carrying no readable timestamp, is reuse.
local used_at = redis.call('GET', used_key)
if used_at then
    local used_at_number = tonumber(used_at)
    if used_at_number and grace_seconds > 0 and (now - used_at_number) <= grace_seconds then
        return 'GRACE'
    end
    return 'REUSED'
end

local stored_jti = redis.call('GET', refresh_key)
if stored_jti ~= expected_jti then
    return 'INVALID'
end

-- Mark the token as consumed, stamping the rotation instant for the grace check
redis.call('SETEX', used_key, used_ttl_seconds, tostring(now))

-- The replacement pair and its index entry land in the same unit as the check.
-- A wipe of every session (ZRANGE, DEL, ZREM) that starts before this script
-- finds the new keys and deletes them; one that deleted the old refresh key
-- first makes this script answer INVALID. Written in later round-trips, the new
-- pair could land after a wipe and outlive a password change.
redis.call('SET', refresh_key, new_refresh_jti, 'EX', refresh_ttl_seconds)
redis.call('SET', access_key, new_access_jti, 'EX', access_ttl_seconds)
redis.call('ZREMRANGEBYSCORE', sessions_key, 0, now)
redis.call('ZADD', sessions_key, now + refresh_ttl_seconds, session_id)
redis.call('EXPIRE', sessions_key, refresh_ttl_seconds)

return 'OK'
