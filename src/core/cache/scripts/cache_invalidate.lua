-- KEYS = version counters to bump, ARGV[1] = version ttl
local versions = {}
for index = 1, #KEYS do
    versions[index] = redis.call('INCR', KEYS[index])
    redis.call('EXPIRE', KEYS[index], ARGV[1])
end
return versions
