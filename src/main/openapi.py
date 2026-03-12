"""OpenAPI and Swagger UI configuration."""

SWAGGER_UI_PARAMETERS: dict[str, object] = {
    # Collapse all tag groups by default on docs load.
    "docExpansion": "none",
    # Hide the Schemas section in the models panel by default.
    "defaultModelsExpandDepth": -1,
    # Show request execution time for each API call.
    "displayRequestDuration": True,
    # Enable the built-in tag search bar (case-sensitive).
    "filter": True,
    # Keep "Try it out" mode enabled without extra clicks.
    "tryItOutEnabled": True,
    # Preserve auth tokens in the browser across page reloads.
    "persistAuthorization": True,
}
