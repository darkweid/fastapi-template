from dataclasses import dataclass

from src.event_log.changes import changed_fields


@dataclass
class Account:
    first_name: str
    is_active: bool
    password_hash: str


def test_only_real_changes_are_reported() -> None:
    """A PATCH that resends the stored value must not fabricate a log entry."""
    account = Account(first_name="Maria", is_active=True, password_hash="x")

    changes = changed_fields(account, {"first_name": "Maria", "is_active": False})

    assert changes == {"is_active": (True, False)}


def test_secrets_never_reach_the_payload() -> None:
    """A password hash in an audit row is a leak that outlives the account."""
    account = Account(first_name="Maria", is_active=True, password_hash="old")

    assert changed_fields(account, {"password_hash": "new"}) == {}


def test_a_field_absent_from_the_instance_is_ignored() -> None:
    """Update payloads carry write-only keys; they are not state transitions."""
    account = Account(first_name="Maria", is_active=True, password_hash="x")

    assert changed_fields(account, {"send_invitation": True}) == {}


def test_a_secret_is_matched_by_name_fragment() -> None:
    """`password_hash` is not the only shape a secret column takes."""

    @dataclass
    class Session:
        refresh_token: str
        otp_secret: str
        api_key_id: str

    session = Session(refresh_token="a", otp_secret="b", api_key_id="c")

    assert (
        changed_fields(
            session,
            {"refresh_token": "x", "otp_secret": "y", "api_key_id": "z"},
        )
        == {}
    )
