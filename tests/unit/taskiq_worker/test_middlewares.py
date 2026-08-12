from typing import Any

import pytest
import sentry_sdk
from taskiq import TaskiqMessage, TaskiqResult

from taskiq_worker.middlewares import SentryMiddleware


def test_on_error_reports_exception_with_task_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given: a task raises inside the worker.
    When: SentryMiddleware.on_error handles the failed attempt.
    Then: the exception is captured with the task name tagged and task id in context.
    """
    captured: dict[str, Any] = {}

    def fake_capture_exception(exception: BaseException) -> None:
        scope = sentry_sdk.get_current_scope()
        captured["exception"] = exception
        captured["tag"] = scope._tags.get("taskiq.task_name")
        captured["context"] = scope._contexts.get("taskiq")

    monkeypatch.setattr(sentry_sdk, "capture_exception", fake_capture_exception)

    message = TaskiqMessage(
        task_id="task-1",
        task_name="send_verification_email",
        labels={},
        labels_types={},
        args=[],
        kwargs={},
    )
    result: TaskiqResult[Any] = TaskiqResult(
        is_err=True,
        log=None,
        return_value=None,
        execution_time=0.01,
        labels={},
    )
    exception = RuntimeError("boom")

    SentryMiddleware().on_error(message, result, exception)

    assert captured["exception"] is exception
    assert captured["tag"] == "send_verification_email"
    assert captured["context"] == {"task_id": "task-1"}
