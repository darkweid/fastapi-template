# Included in every server block and in every location of error_pages.inc. nginx
# drops the add_header directives of the enclosing level as soon as a location
# declares one of its own, so the JSON error pages, which add CORS headers,
# would otherwise answer without these. Not named *.conf on purpose: nginx
# auto-loads /etc/nginx/conf.d/*.conf, and this file belongs inside a block.
#
# No Strict-Transport-Security here: the application's security middleware
# already sends one, and a second header with a different max-age or preload
# flag is a contradiction the browser resolves by taking whichever came first.
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
