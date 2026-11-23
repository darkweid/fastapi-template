-- KEYS = version counters: [1] namespace, [2..] tags in the key's own order
-- ARGV[1] = version ttl
-- Run on a miss, before the value is computed. Returns the generation of every
-- counter, starting the absent ones: the write that follows stores the value
-- only under these generations. An absent counter starts from the server clock,
-- so a counter recreated after an invalidation or an eviction never lands on a
-- generation that addressed a value still stored.
local now = redis.call('TIME')
local generation = now[1] .. string.format('%06d', tonumber(now[2]))
local versions = {}
for index = 1, #KEYS do
    local version = redis.call('GET', KEYS[index])
    if not version then
        version = generation
        redis.call('SET', KEYS[index], version, 'EX', ARGV[1])
    end
    versions[index] = version
end
return versions
