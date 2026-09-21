from typing import Annotated

from fastapi import Depends, Request

from src.core.auth.credentials import (
    RefreshCredentials as RefreshCredentials,
    SessionIdentity as SessionIdentity,
)
from src.core.auth.dependencies import Authenticated, build_realm_auth
from src.core.request_ip import get_client_ip
from src.event_log.actor import Actor
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
get_user_id_from_refresh_token = USER_AUTH.principal_id_from_refresh_token
get_user_id_from_access_token = USER_AUTH.principal_id_from_access_token


def get_user_actor(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> Actor:
    """The signed-in caller as the event log stores them.

    A realm added later declares its own: the pairing of an `ActorType` with an
    id from that realm's store happens here and nowhere else.
    """
    return Actor.user(user.id, ip=get_client_ip(request))
