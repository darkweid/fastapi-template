-- KEYS[1] = version counter key, ARGV[1] = version ttl
local version = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[1])
return version
