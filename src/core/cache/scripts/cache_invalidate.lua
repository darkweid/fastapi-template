-- KEYS = version counters to retire
-- Deleting a counter retires every value addressed through it: a read finds the
-- counter missing and misses, and the next write starts a new generation.
return redis.call('DEL', unpack(KEYS))
