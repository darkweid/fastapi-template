-- KEYS = version counters: [1] namespace, [2..] tags in the key's own order
-- ARGV[1] = "{prefix}:{namespace}", ARGV[2] = suffix, ARGV[3] = payload,
-- ARGV[4] = value ttl, ARGV[5] = version ttl
local version = redis.call('GET', KEYS[1]) or '0'
for index = 2, #KEYS do
    version = version .. '.' .. (redis.call('GET', KEYS[index]) or '0')
end
redis.call('SET', ARGV[1] .. ':v' .. version .. ':' .. ARGV[2], ARGV[3], 'EX', ARGV[4])
-- Every counter this value composed its address from is pushed back out to the
-- full version ttl, so it outlives the value it guards. Without this a counter
-- expiring mid-life resets the version to 0, and the next invalidation increments
-- it straight back onto a value that is still in Redis - resurrecting it.
for index = 1, #KEYS do
    redis.call('EXPIRE', KEYS[index], ARGV[5])
end
return 1
