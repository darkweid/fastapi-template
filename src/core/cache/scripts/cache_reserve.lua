-- KEYS = version counters: [1] namespace, [2..] tags in the key's own order
-- ARGV[1] = reservation ttl
-- Run on a miss, before the value is computed. Returns the generation of every
-- counter, starting the absent ones: the write that follows stores the value
-- only under these generations. An absent counter starts from the server clock,
-- so a counter recreated after an invalidation or an eviction never lands on a
-- generation that addressed a value still stored.
-- A started counter lives for the reservation ttl, which the write raises: a miss
-- that stores nothing must not fill a Redis that evicts nothing. A counter due to
-- expire sooner is extended to it, so this reader gets as long as the first; one
-- with more time left is only read. A full Redis refuses the SET, never the EXPIRE.
local now = redis.call('TIME')
local generation = now[1] .. string.format('%06d', tonumber(now[2]))
local versions = {}
for index = 1, #KEYS do
    local version = redis.call('GET', KEYS[index])
    if not version then
        version = generation
        redis.call('SET', KEYS[index], version, 'EX', ARGV[1])
    elseif redis.call('TTL', KEYS[index]) < tonumber(ARGV[1]) then
        redis.call('EXPIRE', KEYS[index], ARGV[1])
    end
    versions[index] = version
end
return versions
