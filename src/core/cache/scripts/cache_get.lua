-- KEYS[1] = version counter key
-- ARGV[1] = "{prefix}:{namespace}", ARGV[2] = suffix
local version = redis.call('GET', KEYS[1])
if not version then
    version = '0'
end
return redis.call('GET', ARGV[1] .. ':v' .. version .. ':' .. ARGV[2])
