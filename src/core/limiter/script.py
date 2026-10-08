lua_script = """local key = KEYS[1]
local limit = tonumber(ARGV[1])
local expire_time = tonumber(ARGV[2])

local current = tonumber(redis.call('get', key) or "0")
if current > 0 then
 if current + 1 > limit then
 -- 0 means admitted, yet PTTL reads 0 in the key's last millisecond (and -1
 -- on a key whose TTL was removed): a refusal always answers at least 1.
 local ttl = redis.call("PTTL", key)
 if ttl < 1 then
  ttl = 1
 end
 return ttl
 else
        redis.call("INCR", key)
 return 0
 end
else
    redis.call("SET", key, 1,"px",expire_time)
 return 0
end"""
