from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.session import get_session
from src.core.limiter.depends import RateLimiter
from src.core.schemas import SuccessResponse
from src.user.auth.dependencies import (
    get_current_user,
    get_user_id_from_token,
)
from src.user.auth.permissions.checker import require_permission
from src.user.auth.permissions.enum import Permission
from src.user.auth.routers import router as auth_router
from src.user.auth.schemas import UserNewPassword
from src.user.dependencies import get_user_service
from src.user.models import User
from src.user.schemas import (
    UserProfileViewModel,
    UserSummaryViewModel,
)
from src.user.services import UserService
from src.user.usecases.update_password import (
    UpdateUserPasswordUseCase,
    get_update_user_password_use_case,
)

router = APIRouter()

router.include_router(auth_router, prefix="/auth")


@router.get(
    "/me",
    response_model=UserProfileViewModel,
)
async def get_user_profile(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserProfileViewModel:
    """
    Returns the current user's information.
    """
    return UserProfileViewModel.model_validate(current_user)


@router.get("/{user_id}", response_model=UserSummaryViewModel)
async def get_user_info_by_id(
    user_id: UUID,
    # Permission check: this dependency ensures the caller has the VIEW_USERS permission.
    # In most real-world cases you'll also want a domain-specific checker - for example,
    # verifying that the requested user belongs to the same company/group as the requester.
    # Implement such logic as a separate dependency (custom checker) and compose it here.
    current_user: Annotated[User, Depends(require_permission(Permission.VIEW_USERS))],
    user_service: Annotated[UserService, Depends(get_user_service)],
    session: AsyncSession = Depends(get_session),
) -> UserSummaryViewModel:
    user = await user_service.get_single(session, id=user_id)
    return UserSummaryViewModel.model_validate(user)


@router.patch(
    "/me/password",
    response_model=SuccessResponse,
    dependencies=[
        Depends(RateLimiter(times=5, minutes=60, identifier=get_user_id_from_token))
    ],
)
async def update_user_password(
    user_form_data: UserNewPassword,
    current_user: Annotated[User, Depends(get_current_user)],
    use_case: Annotated[
        UpdateUserPasswordUseCase, Depends(get_update_user_password_use_case)
    ],
) -> SuccessResponse:
    """
    Updates the user password.
    """
    return await use_case.execute(data=user_form_data, user_id=current_user.id)
