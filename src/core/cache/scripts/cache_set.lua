-- KEYS = version counters: [1] namespace, [2..] tags in the key's own order
-- ARGV[1] = "{prefix}:{namespace}", ARGV[2] = suffix, ARGV[3] = payload,
-- ARGV[4] = value ttl, ARGV[5] = version ttl,
-- ARGV[6..] = reserved generation of each counter, in the order of KEYS
-- Returns 1 when the value was stored, 0 when a counter is gone or restarted
-- since the reservation: the value may predate the invalidation that did it.
-- A stored value gives its counters the full version ttl back, so a namespace in
-- use stays reachable. The value is written first: a full Redis refuses that SET
-- before anything has changed.
for index = 1, #KEYS do
    if redis.call('GET', KEYS[index]) ~= ARGV[5 + index] then
        return 0
    end
end
local versions = table.concat(ARGV, '.', 6)
redis.call('SET', ARGV[1] .. ':v' .. versions .. ':' .. ARGV[2], ARGV[3], 'EX', ARGV[4])
for index = 1, #KEYS do
    redis.call('EXPIRE', KEYS[index], ARGV[5])
end
return 1
