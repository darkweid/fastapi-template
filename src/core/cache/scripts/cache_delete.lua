-- KEYS = version counters: [1] namespace, [2..] tags in the key's own order
-- ARGV[1] = "{prefix}:{namespace}", ARGV[2] = suffix
-- Same read rule as cache_get: with a counter missing there is no current
-- value to delete.
local versions = {}
for index = 1, #KEYS do
    local version = redis.call('GET', KEYS[index])
    if not version then
        return 0
    end
    versions[index] = version
end
return redis.call('DEL', ARGV[1] .. ':v' .. table.concat(versions, '.') .. ':' .. ARGV[2])
