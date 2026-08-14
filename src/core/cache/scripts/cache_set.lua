-- KEYS = version counters: [1] namespace, [2..] tags in the key's own order
-- ARGV[1] = "{prefix}:{namespace}", ARGV[2] = suffix, ARGV[3] = payload, ARGV[4] = ttl
local version = redis.call('GET', KEYS[1]) or '0'
for index = 2, #KEYS do
    version = version .. '.' .. (redis.call('GET', KEYS[index]) or '0')
end
redis.call('SET', ARGV[1] .. ':v' .. version .. ':' .. ARGV[2], ARGV[3], 'EX', ARGV[4])
return 1
