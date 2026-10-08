-- KEYS = version counters: [1] namespace, [2..] tags in the key's own order
-- ARGV[1] = "{prefix}:{namespace}", ARGV[2] = suffix, ARGV[3] = payload,
-- ARGV[4] = value ttl, ARGV[5] = version ttl
-- An absent counter starts a generation taken from the server clock, so a
-- counter recreated after an invalidation or an eviction never lands on a
-- generation that addressed a value still stored. Present counters keep their
-- generation and get the full version ttl back, so a namespace in use stays
-- reachable.
local now = redis.call('TIME')
local generation = now[1] .. string.format('%06d', tonumber(now[2]))
local versions = {}
for index = 1, #KEYS do
    local version = redis.call('GET', KEYS[index]) or generation
    redis.call('SET', KEYS[index], version, 'EX', ARGV[5])
    versions[index] = version
end
redis.call('SET', ARGV[1] .. ':v' .. table.concat(versions, '.') .. ':' .. ARGV[2], ARGV[3], 'EX', ARGV[4])
return 1
