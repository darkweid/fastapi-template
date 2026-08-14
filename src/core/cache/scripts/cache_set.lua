-- KEYS[1] = version counter key
-- ARGV[1] = "{prefix}:{namespace}", ARGV[2] = suffix, ARGV[3] = payload, ARGV[4] = ttl
local version = redis.call('GET', KEYS[1])
if not version then
    version = '0'
end
redis.call('SET', ARGV[1] .. ':v' .. version .. ':' .. ARGV[2], ARGV[3], 'EX', ARGV[4])
return 1
