from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.user.tasks import _soft_delete_unverified_users


@pytest.mark.asyncio
async def test_soft_delete_unverified_users_success() -> None:
    # Setup mocks
    mock_session = AsyncMock()
    mock_session_context = AsyncMock()
    mock_session_context.__aenter__.return_value = mock_session

    # Mock local_async_session to return our mock_session_context
    with patch("src.user.tasks.local_async_session", return_value=mock_session_context):
        # Mock ApplicationUnitOfWork
        mock_uow = AsyncMock()
        mock_uow.session = mock_session
        mock_uow.users = AsyncMock()
        mock_uow.users.batch_soft_delete.return_value = 5

        # ApplicationUnitOfWork is used as a context manager
        mock_uow.__aenter__.return_value = mock_uow

        with patch("src.user.tasks.ApplicationUnitOfWork", return_value=mock_uow):
            # Execute
            result = await _soft_delete_unverified_users()

            # Assertions
            assert result == 5
            mock_uow.users.batch_soft_delete.assert_called_once()
            mock_uow.commit.assert_called_once()


@pytest.mark.asyncio
async def test_soft_delete_unverified_users_failure() -> None:
    # We use a mock that correctly implements the context manager protocol
    # to avoid the 'coroutine' object error during safe_begin/begin_nested
    class MockSession:
        def __init__(self):
            self.in_transaction = MagicMock(return_value=False)
            self.begin = MagicMock()
            self.begin_nested = MagicMock()
            self.rollback = AsyncMock()
            self.commit = AsyncMock()

    class MockContextManager:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_session = MockSession()
    mock_session.begin.return_value = MockContextManager()
    mock_session.begin_nested.return_value = MockContextManager()

    mock_session_context = AsyncMock()
    mock_session_context.__aenter__.return_value = mock_session

    with patch("src.user.tasks.local_async_session", return_value=mock_session_context):
        from src.core.database.uow import ApplicationUnitOfWork

        uow = ApplicationUnitOfWork(mock_session)  # type: ignore

        mock_users_repo = AsyncMock()
        mock_users_repo.batch_soft_delete.side_effect = Exception("DB Error")

        with patch.object(
            ApplicationUnitOfWork, "users", new_callable=lambda: mock_users_repo
        ):
            with patch.object(
                ApplicationUnitOfWork, "rollback", wraps=uow.rollback
            ) as mock_rollback:
                result = await _soft_delete_unverified_users()

                assert result == 0
                mock_rollback.assert_called_once()
                assert uow.completed is True


@pytest.mark.asyncio
async def test_batch_soft_delete_raises_value_error_on_empty_filters() -> None:
    from src.user.repositories import UserRepository

    repo = UserRepository()
    mock_session = AsyncMock()

    with pytest.raises(ValueError, match="At least one filter must be provided"):
        await repo.batch_soft_delete(mock_session)
