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

-- Mark the token as consumed and remove the active token
redis.call('SETEX', used_key, used_ttl_seconds, '1')
redis.call('DEL', refresh_key)

return 'OK'
