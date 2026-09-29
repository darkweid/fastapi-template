# Shared by the plain-http server in app.conf and the TLS server in
# tls.conf.example, so the two cannot drift apart. Not named *.conf on purpose:
# nginx auto-loads /etc/nginx/conf.d/*.conf, and this file is meant to be
# included from inside a server block, not loaded on its own.

include /etc/nginx/conf.d/error_pages.inc;

# The API speaks these methods and no others. nginx refuses TRACE on its own;
# anything else unknown (PROPFIND, CONNECT, a typo) is refused here, before it
# costs an upstream connection, with the same JSON body as the app's 405.
if ($request_method !~ ^(GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS)$) {
    return 405;
}

# Declared here, at the server level, so both locations below inherit them.
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Request-ID $request_id;
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection $connection_upgrade;

# nginx adds these four itself (security_headers.inc), on its own error pages as
# well as on proxied answers, so the app's copies are dropped here or every
# proxied response would carry each one twice. Only these four: HSTS and the
# Content-Security-Policy come from the app alone, the CSP differing per path.
proxy_hide_header X-Content-Type-Options;
proxy_hide_header X-Frame-Options;
proxy_hide_header Referrer-Policy;
proxy_hide_header Permissions-Policy;

location / {
    proxy_pass http://app_service_upstream;
}

# /live/ stays public: it touches nothing. /ready/ and /health/ each cost a
# Postgres query and a Redis ping, and log a warning per call during an
# outage, so only a caller on a private network reaches them - a load balancer
# or monitor on the provider's private network, a container on app-network.
# Everyone else gets the JSON 404 a missing route answers (error_pages.inc).
# The app-network gateway is denied before the private ranges are allowed:
# Docker forwards the published-port connections it proxies from there, every
# IPv6 client included, so allowing it would open the probes to the internet.
# It is the gateway pinned in infra/docker-compose.yml; change the two together.
# From the box itself, probe the app container directly instead:
# `docker compose exec app wget -qO- http://127.0.0.1:8001/ready/`.
location ~ ^/(health|ready)/$ {
    allow 127.0.0.1;
    allow ::1;
    deny 172.30.0.1;
    allow 10.0.0.0/8;
    allow 172.16.0.0/12;
    allow 192.168.0.0/16;
    allow fc00::/7;
    deny all;
    proxy_pass http://app_service_upstream;
}
