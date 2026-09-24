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

location / {
    proxy_pass http://app_service_upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Request-ID $request_id;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
}
