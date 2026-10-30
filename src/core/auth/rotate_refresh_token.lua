local refresh_key = KEYS[1]
local used_key = KEYS[2]
local access_key = KEYS[3]
local sessions_key = KEYS[4]
local latest_access_key = KEYS[5]
local expected_jti = ARGV[1]
local used_ttl_seconds = ARGV[2]
local grace_seconds = tonumber(ARGV[3])
local session_id = ARGV[4]
local new_refresh_jti = ARGV[5]
local refresh_ttl_seconds = tonumber(ARGV[6])
local new_access_jti = ARGV[7]
local access_ttl_seconds = tonumber(ARGV[8])
local new_refresh_expires_at = tonumber(ARGV[9])
local new_access_expires_at = tonumber(ARGV[10])
local new_issued_at = tonumber(ARGV[11])

-- The Redis server clock, not the app's: every app instance compares reuse
-- against the same clock, so the grace window needs no clock sync between them.
local now = tonumber(redis.call('TIME')[1])

-- A marker names the pair its rotation handed out. Presented again inside the
-- grace window while that pair is still the session's live refresh token, the
-- rotation's answer never reached the client (a lost response, two tabs
-- racing), and the caller re-signs the same pair. Anything else - older, the
-- pair already rotated on, the session gone, a marker without a readable pair -
-- is reuse.
local marker = redis.call('GET', used_key)
if marker then
    local decoded, used = pcall(cjson.decode, marker)
    if decoded and type(used) == 'table' then
        local used_at = tonumber(used['at'])
        if used_at and grace_seconds > 0 and (now - used_at) <= grace_seconds
            and type(used['refresh_jti']) == 'string'
            and redis.call('GET', refresh_key) == used['refresh_jti'] then
            return {'REPLAY', marker}
        end
    end
    return 'REUSED'
end

local stored_jti = redis.call('GET', refresh_key)
if stored_jti ~= expected_jti then
    return 'INVALID'
end

-- Mark the token as consumed with the rotation instant and the pair it was
-- exchanged for: everything a replay inside the grace window needs to re-sign.
redis.call('SETEX', used_key, used_ttl_seconds, cjson.encode({
    at = now,
    refresh_jti = new_refresh_jti,
    refresh_expires_at = new_refresh_expires_at,
    access_jti = new_access_jti,
    access_expires_at = new_access_expires_at,
    issued_at = new_issued_at,
}))

-- The replacement pair and its index entry land in the same unit as the check.
-- A wipe of every session (ZRANGE, DEL, ZREM) that starts before this script
-- finds the new keys and deletes them; one that deleted the old refresh key
-- first makes this script answer INVALID. Written in later round-trips, the new
-- pair could land after a wipe and outlive a password change.
redis.call('SET', refresh_key, new_refresh_jti, 'EX', refresh_ttl_seconds)
redis.call('SET', access_key, new_access_jti, 'EX', access_ttl_seconds)
-- As long as either token of the session can live: logout still names the
-- session by this access token after it expires.
redis.call('SET', latest_access_key, new_access_jti, 'EX', math.max(refresh_ttl_seconds, access_ttl_seconds))
redis.call('ZREMRANGEBYSCORE', sessions_key, 0, now)
redis.call('ZADD', sessions_key, now + refresh_ttl_seconds, session_id)
redis.call('EXPIRE', sessions_key, refresh_ttl_seconds)

return 'OK'
