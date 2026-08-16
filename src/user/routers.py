from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Request,
    Response,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.cache.decorators import cached_route
from src.core.cache.interface import CacheScope
from src.core.database.session import get_session
from src.core.limiter.depends import RateLimiter
from src.core.schemas import SuccessResponse
from src.user.auth.cookies import TokenCookieResponder, get_token_cookie_responder
from src.user.auth.dependencies import (
    get_authenticated_user,
    get_current_user,
    get_user_id_from_token,
)
from src.user.auth.permissions.checker import require_permission
from src.user.auth.permissions.enum import Permission
from src.user.auth.routers import router as auth_router
from src.user.auth.schemas import UserNewPassword
from src.user.auth.token_transport import TokenTransport, get_token_transport
from src.user.cache_keys import user_summary_route_key
from src.user.dependencies import get_user_service
from src.user.models import User
from src.user.schemas import (
    UserProfileUpdateModel,
    UserProfileViewModel,
    UserSummaryViewModel,
)
from src.user.services import UserService
from src.user.usecases.update_password import (
    UpdateUserPasswordUseCase,
    get_update_user_password_use_case,
)
from src.user.usecases.update_profile import (
    UpdateUserProfileUseCase,
    get_update_user_profile_use_case,
)

router = APIRouter()

router.include_router(auth_router, prefix="/auth")


@router.get(
    "/me",
    response_model=UserProfileViewModel,
)
async def get_user_profile(
    # Deliberate opt-out of the admission gate: a blocked or unverified account
    # must still see its own is_verified / is_active state.
    current_user: Annotated[User, Depends(get_authenticated_user)],
) -> UserProfileViewModel:
    """
    Returns the current user's information.
    """
    return UserProfileViewModel.model_validate(current_user)


@router.get("/{user_id}", response_model=UserSummaryViewModel)
@cached_route(
    key_builder=user_summary_route_key,
    ttl=60,
    scope=CacheScope.PUBLIC,
)
async def get_user_info_by_id(
    user_id: UUID,
    request: Request,
    response: Response,
    # Permission check: this dependency ensures the caller has the VIEW_USERS permission.
    # In most real-world cases you'll also want a domain-specific checker - for example,
    # verifying that the requested user belongs to the same company/group as the requester.
    # Implement such logic as a separate dependency (custom checker) and compose it here.
    current_user: Annotated[User, Depends(require_permission(Permission.VIEW_USERS))],
    user_service: Annotated[UserService, Depends(get_user_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserSummaryViewModel:
    """
    Returns public information about a user by their identifier.
    """
    user = await user_service.get_single_or_404(session, id=user_id)
    return UserSummaryViewModel.model_validate(user)


@router.patch("/me", response_model=UserProfileViewModel)
async def update_user_profile(
    user_form_data: UserProfileUpdateModel,
    current_user: Annotated[User, Depends(get_current_user)],
    use_case: Annotated[
        UpdateUserProfileUseCase, Depends(get_update_user_profile_use_case)
    ],
) -> UserProfileViewModel:
    """
    Updates the current user's profile.
    """
    return await use_case.execute(data=user_form_data, user_id=current_user.id)


@router.patch(
    "/me/password",
    response_model=SuccessResponse,
    dependencies=[
        Depends(RateLimiter(times=5, minutes=60, identifier=get_user_id_from_token))
    ],
)
async def update_user_password(
    response: Response,
    user_form_data: UserNewPassword,
    current_user: Annotated[User, Depends(get_current_user)],
    transport: Annotated[TokenTransport, Depends(get_token_transport)],
    responder: Annotated[TokenCookieResponder, Depends(get_token_cookie_responder)],
    use_case: Annotated[
        UpdateUserPasswordUseCase, Depends(get_update_user_password_use_case)
    ],
) -> SuccessResponse:
    """
    Updates the user password. Requires the current password and signs out every
    active session, including this one.

    The auth cookies are expired along with the server-side sessions: leaving the
    browser holding a refresh cookie that no longer resolves would show up as a
    silent, unexplainable logout on its next refresh.
    """
    result = await use_case.execute(data=user_form_data, user_id=current_user.id)
    responder.clear(response, transport)
    return result
