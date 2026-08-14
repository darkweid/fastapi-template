# Shared by the plain-http server in app.conf and the TLS server in
# tls.conf.example, so the two cannot drift apart. Not named *.conf on purpose:
# nginx auto-loads /etc/nginx/conf.d/*.conf, and this file is meant to be
# included from inside a server block, not loaded on its own.

# Errors nginx answers itself never reach the app, so its stock HTML pages would
# break the {"error","message"} contract every other response follows: an
# oversized body (413) and an app that is down or too slow (502/504).
error_page 413 = @error_payload_too_large;
error_page 502 = @error_bad_gateway;
error_page 504 = @error_gateway_timeout;

# The CORS headers below are what makes the JSON readable: without them a
# browser blocks the response and a cross-origin SPA sees a network error
# instead of the body. The origin is reflected rather than matched against a
# list, which nginx does not have - these three responses carry a fixed string
# and no application data, and the real allowlist stays in the app's CORS
# middleware, which answers every non-error request.
location @error_payload_too_large {
    default_type application/json;
    add_header Access-Control-Allow-Origin $http_origin always;
    add_header Access-Control-Allow-Credentials true always;
    add_header Vary Origin always;
    return 413 '{"error":"Payload too large","message":"Request body exceeds the maximum allowed size"}';
}

location @error_bad_gateway {
    default_type application/json;
    add_header Access-Control-Allow-Origin $http_origin always;
    add_header Access-Control-Allow-Credentials true always;
    add_header Vary Origin always;
    return 502 '{"error":"Bad gateway","message":"The service is temporarily unreachable"}';
}

location @error_gateway_timeout {
    default_type application/json;
    add_header Access-Control-Allow-Origin $http_origin always;
    add_header Access-Control-Allow-Credentials true always;
    add_header Vary Origin always;
    return 504 '{"error":"Gateway timeout","message":"The service took too long to respond"}';
}

location / {
    proxy_pass http://app_service_upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
}

location /docs {
    proxy_pass http://app_service_upstream$request_uri;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location /openapi.json {
    proxy_pass http://app_service_upstream$request_uri;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
