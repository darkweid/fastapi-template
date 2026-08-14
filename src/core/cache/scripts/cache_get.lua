-- KEYS = version counters: [1] namespace, [2..] tags in the key's own order
-- ARGV[1] = "{prefix}:{namespace}", ARGV[2] = suffix
local version = redis.call('GET', KEYS[1]) or '0'
for index = 2, #KEYS do
    version = version .. '.' .. (redis.call('GET', KEYS[index]) or '0')
end
return redis.call('GET', ARGV[1] .. ':v' .. version .. ':' .. ARGV[2])
