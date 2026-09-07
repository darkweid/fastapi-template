local refresh_key = KEYS[1]
local used_key = KEYS[2]
local expected_jti = ARGV[1]
local used_ttl_seconds = ARGV[2]
local grace_seconds = tonumber(ARGV[3])

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

-- Atomically check and remove the active token
local stored_jti = redis.call('GET', refresh_key)
if stored_jti ~= expected_jti then
    return 'INVALID'
end

-- Mark the token as consumed, stamping the rotation instant for the grace check
redis.call('SETEX', used_key, used_ttl_seconds, tostring(now))
redis.call('DEL', refresh_key)

return 'OK'
