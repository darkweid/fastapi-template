from src.core.auth.credentials import (
    RefreshCredentials as RefreshCredentials,
    SessionIdentity as SessionIdentity,
)
from src.core.auth.dependencies import Authenticated, build_realm_auth
from src.user.auth.realm import USER_AUTH_REALM
from src.user.models import User
from src.user.policies import ensure_can_use_session
from src.user.repositories import UserRepository

USER_AUTH = build_realm_auth(
    realm=USER_AUTH_REALM,
    principal_type=User,
    repository_factory=UserRepository,
    admission=ensure_can_use_session,
)

AuthenticatedUser = Authenticated[User]

get_token_cookie_responder = USER_AUTH.cookie_responder
get_refresh_credentials = USER_AUTH.refresh_credentials
verify_csrf = USER_AUTH.verify_csrf
authenticate_access_token = USER_AUTH.authenticate
get_current_user = USER_AUTH.current_principal
get_current_user_with_session = USER_AUTH.current_principal_with_session
get_authenticated_user = USER_AUTH.authenticated_principal
get_logout_identity = USER_AUTH.logout_identity
get_access_by_refresh_token = USER_AUTH.access_by_refresh
get_user_id_from_token = USER_AUTH.principal_id_from_token
