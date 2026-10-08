-- KEYS = version counters: [1] namespace, [2..] tags in the key's own order
-- ARGV[1] = "{prefix}:{namespace}", ARGV[2] = suffix
-- A missing counter is a miss, never version 0: the counter may have been
-- invalidated or evicted while a value written under it is still stored.
local versions = {}
for index = 1, #KEYS do
    local version = redis.call('GET', KEYS[index])
    if not version then
        return nil
    end
    versions[index] = version
end
return redis.call('GET', ARGV[1] .. ':v' .. table.concat(versions, '.') .. ':' .. ARGV[2])
