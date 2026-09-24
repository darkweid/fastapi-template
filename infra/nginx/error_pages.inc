# Included from every server block, the default one in app.conf and
# tls.conf.example included. Not named *.conf on purpose: nginx auto-loads
# /etc/nginx/conf.d/*.conf, and this file belongs inside a server block.

# Errors nginx answers itself never reach the app, so its stock HTML pages would
# break the {"code","message"} contract every other response follows: a request
# nginx cannot parse (400, and 494 for oversized headers), a method outside the
# API's set (405, TRACE included), an overlong URI (414), an oversized body
# (413), a Host that differs from the TLS name the connection was opened for
# (421) and an app that is down or too slow (502/504). A malformed request line
# (`/%00`) is refused before the Host header is read, by whichever server is the
# default one, which is why that server carries these pages too.
#
# 400 and 414 point at URIs, not named locations: they are raised while the
# request line is still being parsed, and nginx refuses to jump to a named
# location from a request with no URI yet ("empty URI in redirect to named
# location"), answering its own HTML 500 instead. The two locations are not
# `internal`, since an internal one answers a direct request with nginx's HTML
# 404; asked for directly, each just returns its fixed JSON.
error_page 400 494 /__nginx_error/bad_request;
error_page 405 = @error_method_not_allowed;
error_page 413 = @error_payload_too_large;
error_page 414 /__nginx_error/uri_too_long;
error_page 421 = @error_misdirected_request;
error_page 502 = @error_bad_gateway;
error_page 504 = @error_gateway_timeout;

# The CORS headers below are what makes the JSON readable: without them a
# browser blocks the response and a cross-origin SPA sees a network error
# instead of the body. The origin is reflected rather than matched against a
# list, which nginx does not have - these responses carry a fixed string
# and no application data, and the real allowlist stays in the app's CORS
# middleware, which answers every non-error request.
location = /__nginx_error/bad_request {
    default_type application/json;
    add_header Access-Control-Allow-Origin $http_origin always;
    add_header Access-Control-Allow-Credentials true always;
    add_header Vary Origin always;
    add_header X-Request-ID $request_id always;
    return 400 '{"code":"bad_request","message":"The request is malformed"}';
}

location @error_method_not_allowed {
    default_type application/json;
    add_header Access-Control-Allow-Origin $http_origin always;
    add_header Access-Control-Allow-Credentials true always;
    add_header Vary Origin always;
    add_header X-Request-ID $request_id always;
    add_header Allow "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS" always;
    return 405 '{"code":"method_not_allowed","message":"Method not allowed"}';
}

location @error_payload_too_large {
    default_type application/json;
    add_header Access-Control-Allow-Origin $http_origin always;
    add_header Access-Control-Allow-Credentials true always;
    add_header Vary Origin always;
    add_header X-Request-ID $request_id always;
    return 413 '{"code":"payload_too_large","message":"Request body exceeds the maximum allowed size"}';
}

location = /__nginx_error/uri_too_long {
    default_type application/json;
    add_header Access-Control-Allow-Origin $http_origin always;
    add_header Access-Control-Allow-Credentials true always;
    add_header Vary Origin always;
    add_header X-Request-ID $request_id always;
    return 414 '{"code":"uri_too_long","message":"Request URI is too long"}';
}

location @error_misdirected_request {
    default_type application/json;
    add_header Access-Control-Allow-Origin $http_origin always;
    add_header Access-Control-Allow-Credentials true always;
    add_header Vary Origin always;
    add_header X-Request-ID $request_id always;
    return 421 '{"code":"misdirected_request","message":"The request was sent to the wrong host"}';
}

location @error_bad_gateway {
    default_type application/json;
    add_header Access-Control-Allow-Origin $http_origin always;
    add_header Access-Control-Allow-Credentials true always;
    add_header Vary Origin always;
    add_header X-Request-ID $request_id always;
    return 502 '{"code":"bad_gateway","message":"The service is temporarily unreachable"}';
}

location @error_gateway_timeout {
    default_type application/json;
    add_header Access-Control-Allow-Origin $http_origin always;
    add_header Access-Control-Allow-Credentials true always;
    add_header Vary Origin always;
    add_header X-Request-ID $request_id always;
    return 504 '{"code":"gateway_timeout","message":"The service took too long to respond"}';
}
