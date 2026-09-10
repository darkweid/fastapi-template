local challenge_key = KEYS[1]
local presented_value = ARGV[1]

-- Compare and delete in one operation: spread over two round-trips, two
-- concurrent requests holding the same value both read it as live and both
-- proceed.
local active = redis.call('GET', challenge_key)
if active ~= presented_value then
    return 'INVALID'
end

-- Not GETDEL: that deletes on a mismatch too, so anyone presenting a
-- superseded value would retire the challenge the owner is still waiting on.
redis.call('DEL', challenge_key)

return 'OK'
