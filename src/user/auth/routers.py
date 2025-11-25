from typing import Annotated

from fastapi import APIRouter, Depends, Request

from src.core.limiter.depends import RateLimiter
from src.core.schemas import SuccessResponse, TokenModel, TokenRefreshModel
from src.main.config import config
from src.user.auth.dependencies import (
    get_access_by_refresh_token,
    get_user_id_from_token,
)
from src.user.auth.schemas import (
    CreateUserModel,
    LoginUserModel,
    ResendVerificationModel,
    ResetPasswordModel,
    SendResetPasswordRequestModel,
)
from src.user.auth.security import create_access_token
from src.user.auth.usecases.login import LoginUserUseCase, get_login_user_use_case
from src.user.auth.usecases.register import RegisterUseCase, get_register_use_case
from src.user.auth.usecases.resend_verification import (
    SendVerificationUseCase,
    get_send_verification_use_case,
)
from src.user.auth.usecases.reset_password_confirm import (
    ResetPasswordConfirmUseCase,
    get_reset_password_confirm_use_case,
)
from src.user.auth.usecases.reset_password_request import (
    ResetPasswordRequestUseCase,
    get_reset_password_request_use_case,
)
from src.user.auth.usecases.verify_email import (
    VerifyEmailUseCase,
    get_verify_email_use_case,
)
from src.user.models import User
from src.user.schemas import (
    UserProfileViewModel,
)

router = APIRouter()


@router.post(
    "/register",
    status_code=201,
    response_model=UserProfileViewModel,
    dependencies=[Depends(RateLimiter(times=10, minutes=10))],
)
async def signup_user(
    request: Request,
    user_form_data: CreateUserModel,
    use_case: Annotated[RegisterUseCase, Depends(get_register_use_case)],
) -> UserProfileViewModel:
    """
    Create a new user account.
    """
    return await use_case.execute(
        data=user_form_data, request_base_url=request.base_url
    )


@router.post(
    "/verification-email",
    status_code=200,
    dependencies=[
        Depends(
            RateLimiter(times=3, minutes=config.jwt.VERIFICATION_TOKEN_EXPIRE_MINUTES)
        )
    ],
)
async def send_verification_email(
    request: Request,
    data: ResendVerificationModel,
    use_case: Annotated[
        SendVerificationUseCase, Depends(get_send_verification_use_case)
    ],
) -> SuccessResponse:
    """
    Sends the verification link to the user's email.
    """
    return await use_case.execute(data=data, request_base_url=request.base_url)


@router.get("/verify", status_code=200)
async def verify_email(
    token: str,
    use_case: Annotated[VerifyEmailUseCase, Depends(get_verify_email_use_case)],
) -> SuccessResponse:
    """
    Verifies the user's email using the provided token.
    """
    return await use_case.execute(token=token)


@router.post(
    "/login",
    response_model=TokenModel,
    dependencies=[Depends(RateLimiter(times=2, seconds=60))],
)
async def login_user(
    login_form_data: LoginUserModel,
    use_case: Annotated[LoginUserUseCase, Depends(get_login_user_use_case)],
) -> TokenModel:
    """
    Authenticate user and return tokens.
    """
    return await use_case.execute(data=login_form_data)


@router.post(
    "/login/refresh",
    response_model=TokenRefreshModel,
    dependencies=[
        Depends(  # IP-based rate limiting
            RateLimiter(
                times=20,
                minutes=15,
            )
        ),
        Depends(  # User-based rate limiting
            RateLimiter(
                times=5,
                minutes=15,
                identifier=get_user_id_from_token,
            )
        ),
    ],
)
async def get_access_by_refresh(
    current_user: Annotated[User, Depends(get_access_by_refresh_token)],
) -> TokenRefreshModel:
    """
    Refresh the access token using a valid refresh token.
    """
    return TokenRefreshModel(
        access_token=await create_access_token({"sub": str(current_user.id)})
    )


@router.post(
    "/password/reset",
    response_model=SuccessResponse,
)
async def send_reset_password_request(
    request: Request,
    data: SendResetPasswordRequestModel,
    use_case: Annotated[
        ResetPasswordRequestUseCase, Depends(get_reset_password_request_use_case)
    ],
) -> SuccessResponse:
    return await use_case.execute(data=data, request_base_url=request.base_url)


@router.put("/password/reset/confirm", response_model=SuccessResponse)
async def confirm_reset_password_request(
    data: ResetPasswordModel,
    use_case: Annotated[
        ResetPasswordConfirmUseCase, Depends(get_reset_password_confirm_use_case)
    ],
) -> SuccessResponse:
    return await use_case.execute(data=data)
