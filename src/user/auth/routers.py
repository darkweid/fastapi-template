from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request, Response

from src.core.limiter.depends import RateLimiter
from src.core.schemas import SuccessResponse, TokenModel
from src.main.config import config
from src.user.auth.cookies import TokenCookieResponder, get_token_cookie_responder
from src.user.auth.dependencies import (
    SessionIdentity,
    get_access_by_refresh_token,
    get_logout_identity,
    get_user_id_from_token,
    verify_csrf,
)
from src.user.auth.jwt_payload_schema import JWTPayload
from src.user.auth.schemas import (
    CreateUserModel,
    LoginUserModel,
    LogoutRequestModel,
    ResendVerificationModel,
    ResetPasswordModel,
    SendResetPasswordRequestModel,
)
from src.user.auth.token_transport import TokenTransport, get_token_transport
from src.user.auth.usecases.get_access_by_refresh import (
    GetTokensByRefreshUserUseCase,
    get_tokens_by_refresh_user_use_case,
)
from src.user.auth.usecases.login import LoginUserUseCase, get_login_user_use_case
from src.user.auth.usecases.logout import LogoutUseCase, get_logout_use_case
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
    response: Response,
    transport: Annotated[TokenTransport, Depends(get_token_transport)],
    responder: Annotated[TokenCookieResponder, Depends(get_token_cookie_responder)],
    use_case: Annotated[LoginUserUseCase, Depends(get_login_user_use_case)],
) -> TokenModel:
    """
    Authenticate user and return tokens.

    By default the refresh token is returned as an httponly cookie. Native clients
    that store tokens themselves should send `X-Token-Transport: body` to receive it
    in the response body instead.
    """
    tokens = await use_case.execute(data=login_form_data)
    return responder.apply(tokens, response, transport)


@router.post(
    "/login/refresh",
    response_model=TokenModel,
    dependencies=[
        Depends(  # IP-based rate limiting: bounds an unauthenticated flood
            RateLimiter(
                times=20,
                minutes=15,
            )
        ),
        # The CSRF gate must precede the user-scoped limiter: its identifier reads the
        # refresh cookie and verifies it, so a forged cross-site request would
        # otherwise burn the victim's refresh budget (and reach reuse detection)
        # before being rejected. FastAPI caches get_refresh_credentials, so the token
        # is still resolved only once per request.
        Depends(verify_csrf),
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
    response: Response,
    user_and_payload: Annotated[
        tuple[User, JWTPayload], Depends(get_access_by_refresh_token)
    ],
    transport: Annotated[TokenTransport, Depends(get_token_transport)],
    responder: Annotated[TokenCookieResponder, Depends(get_token_cookie_responder)],
    use_case: Annotated[
        GetTokensByRefreshUserUseCase, Depends(get_tokens_by_refresh_user_use_case)
    ],
) -> TokenModel:
    """
    Refresh the access token using a valid refresh token.

    Browser clients send the refresh cookie together with the `X-CSRF-Token` header.
    Native clients send the refresh token in the Authorization header.
    """
    current_user, old_payload = user_and_payload
    tokens = await use_case.execute(user=current_user, old_token_payload=old_payload)
    return responder.apply(tokens, response, transport)


@router.post(
    "/logout",
    response_model=SuccessResponse,
)
async def logout_user(
    response: Response,
    identity: Annotated[SessionIdentity | None, Depends(get_logout_identity)],
    transport: Annotated[TokenTransport, Depends(get_token_transport)],
    responder: Annotated[TokenCookieResponder, Depends(get_token_cookie_responder)],
    use_case: Annotated[LogoutUseCase, Depends(get_logout_use_case)],
    data: Annotated[LogoutRequestModel | None, Body()] = None,
) -> SuccessResponse:
    """
    Invalidate the current session or all user sessions and clear the auth cookies.

    Works with an expired access token, so a client can always log out.
    """
    if identity is not None:
        await use_case.execute(
            user_id=identity.user_id,
            session_id=identity.session_id,
            terminate_all_sessions=(
                data.terminate_all_sessions if data is not None else False
            ),
        )
    responder.clear(response, transport)
    return SuccessResponse(success=True)


@router.post(
    "/password/reset",
    response_model=SuccessResponse,
    dependencies=[
        Depends(
            RateLimiter(
                times=3,
                minutes=15,
            )
        )
    ],
)
async def send_reset_password_request(
    request: Request,
    data: SendResetPasswordRequestModel,
    use_case: Annotated[
        ResetPasswordRequestUseCase, Depends(get_reset_password_request_use_case)
    ],
) -> SuccessResponse:
    """
    Sends a password reset link to the user's email.
    """
    return await use_case.execute(data=data, request_base_url=request.base_url)


@router.put(
    "/password/reset/confirm",
    response_model=SuccessResponse,
    dependencies=[
        Depends(
            RateLimiter(
                times=5,
                minutes=15,
            )
        )
    ],
)
async def confirm_reset_password_request(
    data: ResetPasswordModel,
    use_case: Annotated[
        ResetPasswordConfirmUseCase, Depends(get_reset_password_confirm_use_case)
    ],
) -> SuccessResponse:
    """
    Sets a new password using a valid password reset token.
    """
    return await use_case.execute(data=data)
