from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ops.check_env import (
    PLACEHOLDER_MARKER,
    SECRET_KEY_PATTERN,
    collect_backup_problems,
    collect_problems,
    main,
    parse_env,
    parse_raw_env,
)

REPO_ROOT = Path(__file__).resolve().parents[4]

EXAMPLE = {
    "DEBUG": "false",
    "VALIDATE_CERTS": "True",
    "COOKIE_SECURE": "true",
    "POSTGRES_PASSWORD": "secure_password",
    "PUBLIC_BASE_URL": "http://localhost:3000",
    "JWT_USER_SECRET_KEY": "example-jwt-user-secret-key-not-real",
    "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
    "EMAIL_USER": "user@example.com",
    "RESOURCE_PROFILE": "",
}


def _deployable_env() -> dict[str, str]:
    return {
        "DEBUG": "false",
        "VALIDATE_CERTS": "True",
        "COOKIE_SECURE": "true",
        "POSTGRES_PASSWORD": "a-real-database-password",
        "PUBLIC_BASE_URL": "https://app.example.com",
        "JWT_USER_SECRET_KEY": "a-real-jwt-user-signing-secret-key",
        "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
        "EMAIL_USER": "user@example.com",
        "RESOURCE_PROFILE": "production",
    }


def test_deployable_env_has_no_problems() -> None:
    assert collect_problems(EXAMPLE, _deployable_env()) == []


def test_missing_key_is_reported() -> None:
    actual = _deployable_env()
    del actual["COOKIE_SECURE"]

    problems = collect_problems(EXAMPLE, actual)

    assert problems == ["Missing keys in .env: COOKIE_SECURE"]


def test_secret_copied_from_example_is_reported() -> None:
    actual = _deployable_env()
    actual["POSTGRES_PASSWORD"] = EXAMPLE["POSTGRES_PASSWORD"]

    problems = collect_problems(EXAMPLE, actual)

    assert problems == [
        "POSTGRES_PASSWORD still holds the placeholder value from .env.example"
    ]


def test_placeholder_marker_is_reported_even_when_example_changed() -> None:
    actual = _deployable_env()
    actual["JWT_USER_SECRET_KEY"] = "our-own-jwt-user-secret-key-not-real"

    problems = collect_problems(EXAMPLE, actual)

    assert problems == [
        "JWT_USER_SECRET_KEY still holds the placeholder value from .env.example"
    ]


def test_expiry_setting_named_after_a_token_is_not_a_secret() -> None:
    """ACCESS_TOKEN_EXPIRE_MINUTES is meant to keep the example value."""
    actual = _deployable_env()
    actual["ACCESS_TOKEN_EXPIRE_MINUTES"] = EXAMPLE["ACCESS_TOKEN_EXPIRE_MINUTES"]

    assert collect_problems(EXAMPLE, actual) == []


def test_non_secret_value_matching_example_is_allowed() -> None:
    actual = _deployable_env()
    actual["EMAIL_USER"] = EXAMPLE["EMAIL_USER"]

    assert collect_problems(EXAMPLE, actual) == []


def test_debug_enabled_is_reported() -> None:
    actual = _deployable_env()
    actual["DEBUG"] = "true"

    problems = collect_problems(EXAMPLE, actual)

    assert problems == ["DEBUG=true is not allowed outside local development"]


def test_certificate_validation_disabled_is_reported() -> None:
    actual = _deployable_env()
    actual["VALIDATE_CERTS"] = "False"

    problems = collect_problems(EXAMPLE, actual)

    assert problems == ["VALIDATE_CERTS=False is not allowed outside local development"]


def test_insecure_cookies_are_reported() -> None:
    actual = _deployable_env()
    actual["COOKIE_SECURE"] = "false"

    problems = collect_problems(EXAMPLE, actual)

    assert problems == ["COOKIE_SECURE=false is not allowed outside local development"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param("DEBUG", "on", id="debug-on"),
        pytest.param("DEBUG", "y", id="debug-y"),
        pytest.param("DEBUG", "t", id="debug-t"),
        pytest.param("COOKIE_SECURE", "off", id="cookies-off"),
        pytest.param("COOKIE_SECURE", "n", id="cookies-n"),
        pytest.param("VALIDATE_CERTS", "f", id="certs-f"),
    ],
)
def test_every_boolean_spelling_pydantic_accepts_is_reported(
    key: str, value: str
) -> None:
    """
    pydantic parses on/off/t/f/y/n too, so a check that only knows "true" is a
    gate anyone can walk past by spelling the value differently.
    """
    actual = _deployable_env()
    actual[key] = value

    problems = collect_problems(EXAMPLE, actual)

    assert problems == [f"{key}={value} is not allowed outside local development"]


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://localhost:3000", id="localhost"),
        pytest.param("http://127.0.0.1:8000", id="loopback-ipv4"),
        pytest.param("http://[::1]:3000", id="loopback-ipv6"),
        pytest.param("http://0.0.0.0:3000", id="unspecified"),  # noqa: S104
    ],
)
def test_local_public_base_url_is_reported(url: str) -> None:
    actual = _deployable_env()
    actual["PUBLIC_BASE_URL"] = url

    problems = collect_problems(EXAMPLE, actual)

    assert problems == [
        f"PUBLIC_BASE_URL={url} points at localhost: users receive email links "
        "they cannot open"
    ]


def test_public_base_url_on_a_real_host_is_allowed() -> None:
    actual = _deployable_env()
    actual["PUBLIC_BASE_URL"] = "https://app.example.com"

    assert collect_problems(EXAMPLE, actual) == []


@pytest.mark.parametrize("value", ["", "  "])
def test_an_empty_resource_profile_is_reported(value: str) -> None:
    """The example leaves it blank for a local stack; a box left that way would
    be refused by deploy.sh after the gate had passed it."""
    actual = _deployable_env()
    actual["RESOURCE_PROFILE"] = value

    assert collect_problems(EXAMPLE, actual) == [
        "RESOURCE_PROFILE is empty: a deploy needs it set"
    ]


def test_shipped_env_example_is_rejected_as_a_deploy_config() -> None:
    """
    The example is a working local config, never a deployable one. Copying it
    unchanged has to fail loudly, and every secret in it has to be named.

    S3_ACCESS_KEY_ID/S3_SECRET_ACCESS_KEY are deliberately absent here: the
    example ships S3_ENABLED=false, and get_s3_adapter refuses to build a
    client in that state, so the placeholder values are inert.
    """
    example = parse_env(REPO_ROOT / ".env.example")

    problems = collect_problems(example, example)

    flagged = {problem.split()[0].split("=")[0] for problem in problems}
    assert {
        "POSTGRES_PASSWORD",
        "REDIS_PASSWORD",
        "EMAIL_PASSWORD",
        "DOCS_PASSWORD",
        "CSRF_SECRET_KEY",
        "JWT_USER_SECRET_KEY",
        "JWT_USER_VERIFY_SECRET_KEY",
        "JWT_USER_RESET_PASSWORD_SECRET_KEY",
        "SENTRY_DSN",
        "PUBLIC_BASE_URL",
        "RESOURCE_PROFILE",
    } <= flagged
    assert "S3_ACCESS_KEY_ID" not in flagged
    assert "S3_SECRET_ACCESS_KEY" not in flagged


@pytest.mark.parametrize("name", [".env.example", ".env.backup.example"])
def test_every_secret_in_the_example_carries_the_placeholder_marker(name: str) -> None:
    """
    Equality with .env.example is not enough on its own: editing the example is
    the first thing a fork does, and that would silently disable the check.
    """
    example = parse_env(REPO_ROOT / name)

    unmarked = [
        key
        for key, value in example.items()
        if SECRET_KEY_PATTERN.search(key) and PLACEHOLDER_MARKER not in value.lower()
    ]

    assert unmarked == []


def test_s3_placeholder_is_ignored_when_s3_disabled() -> None:
    """get_s3_adapter refuses to build a client while S3 is disabled, so a
    leftover placeholder credential in that state is harmless."""
    example = {
        **EXAMPLE,
        "S3_ENABLED": "false",
        "S3_ACCESS_KEY_ID": "example-s3-access-key-id-not-real",
    }
    actual = {
        **_deployable_env(),
        "S3_ENABLED": "false",
        "S3_ACCESS_KEY_ID": "example-s3-access-key-id-not-real",
    }

    assert collect_problems(example, actual) == []


def test_omitted_s3_keys_are_allowed_when_s3_disabled() -> None:
    """Credentials are optional while S3 is disabled, so a deploy that drops
    the S3 keys entirely must not be rejected as missing them."""
    example = {
        **EXAMPLE,
        "S3_ENABLED": "false",
        "S3_ACCESS_KEY_ID": "example-s3-access-key-id-not-real",
    }
    actual = {**_deployable_env(), "S3_ENABLED": "false"}
    actual.pop("S3_ACCESS_KEY_ID", None)

    assert collect_problems(example, actual) == []


def test_omitted_s3_keys_are_reported_when_s3_enabled() -> None:
    example = {
        **EXAMPLE,
        "S3_ENABLED": "false",
        "S3_ACCESS_KEY_ID": "example-s3-access-key-id-not-real",
    }
    actual = {**_deployable_env(), "S3_ENABLED": "true"}
    actual.pop("S3_ACCESS_KEY_ID", None)

    assert collect_problems(example, actual) == [
        "Missing keys in .env: S3_ACCESS_KEY_ID"
    ]


def test_s3_placeholder_is_reported_when_s3_enabled() -> None:
    example = {
        **EXAMPLE,
        "S3_ENABLED": "false",
        "S3_ACCESS_KEY_ID": "example-s3-access-key-id-not-real",
    }
    actual = {
        **_deployable_env(),
        "S3_ENABLED": "true",
        "S3_ACCESS_KEY_ID": "example-s3-access-key-id-not-real",
    }

    problems = collect_problems(example, actual)

    assert problems == [
        "S3_ACCESS_KEY_ID still holds the placeholder value from .env.example"
    ]


def test_parse_env_reads_quotes_comments_and_embedded_equals(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "PLAIN=value",
                'QUOTED="quoted value"',
                "SINGLE='single'",
                "  SPACED  =  padded  ",
                "DSN=postgresql://user:pass@host:5432/db?ssl=require",
                "NOT_A_PAIR",
            ]
        )
    )

    assert parse_env(env_file) == {
        "PLAIN": "value",
        "QUOTED": "quoted value",
        "SINGLE": "single",
        "SPACED": "padded",
        "DSN": "postgresql://user:pass@host:5432/db?ssl=require",
    }


@pytest.mark.parametrize(
    ("line", "value"),
    [
        ("DEBUG=true # temp", "true"),
        ("DEBUG=true\t# temp", "true"),
        ("PASSWORD=abc#def", "abc#def"),
        ('QUOTED="a # b" # note', "a # b"),
        ("SINGLE='a # b' # note", "a # b"),
        ('ESCAPED="say \\"hi\\"" # note', 'say \\"hi\\"'),
        ("EMPTY= # nothing", ""),
    ],
)
def test_parse_env_strips_inline_comments_the_way_compose_does(
    tmp_path: Path, line: str, value: str
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(line)

    [parsed] = parse_env(env_file).values()

    assert parsed == value


def test_debug_enabled_behind_an_inline_comment_is_reported(tmp_path: Path) -> None:
    """compose starts the container with DEBUG=true here; the gate once read
    "true # temp", recognised no boolean and let the deploy through."""
    env_file = tmp_path / ".env"
    env_file.write_text("DEBUG=true # temp\n")

    problems = collect_problems({"DEBUG": "false"}, parse_env(env_file))

    assert problems == ["DEBUG=true is not allowed outside local development"]


def test_get_prints_a_value_the_way_compose_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """deploy.sh and the Makefile pick the resource profile with this; a quote
    or a comment left on the value would name a profile that does not exist."""
    (tmp_path / ".env").write_text('DEBUG=false\nRESOURCE_PROFILE="stage"  # 4 CPU\n')
    monkeypatch.chdir(tmp_path)

    main(["--get", "RESOURCE_PROFILE"])

    assert capsys.readouterr().out == "stage\n"


def test_get_prints_an_empty_line_for_an_absent_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".env").write_text("DEBUG=false\n")
    monkeypatch.chdir(tmp_path)

    main(["--get", "RESOURCE_PROFILE"])

    assert capsys.readouterr().out == "\n"


def test_get_fails_without_an_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Makefile hides this error and reads no profile, which is what the dev
    stack without a .env needs; deploy.sh stops at the gate before it."""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as raised:
        main(["--get", "RESOURCE_PROFILE"])

    assert raised.value.code == 1


@pytest.mark.parametrize(
    "argv", [["--get"], ["--get", "A", "B"], ["--set", "A"], ["--enabled"]]
)
def test_an_unknown_invocation_is_a_usage_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(argv)

    assert raised.value.code == 2


BACKUP_EXAMPLE = {
    "PGBACKREST_REPO1_S3_BUCKET": "",
    "PGBACKREST_REPO1_S3_ENDPOINT": "",
    "PGBACKREST_REPO1_S3_REGION": "",
    "PGBACKREST_REPO1_S3_KEY": "backup-s3-key-not-real",
    "PGBACKREST_REPO1_S3_KEY_SECRET": "backup-s3-secret-not-real",
    "PGBACKREST_REPO1_CIPHER_PASS": "backup-cipher-pass-not-real",
    "BACKUP_HEARTBEAT_URL": "",
    "BACKUP_ARCHIVE_HEARTBEAT_URL": "",
}


def _deployable_backup_env() -> dict[str, str]:
    return {
        "PGBACKREST_REPO1_S3_BUCKET": "app-backups",
        "PGBACKREST_REPO1_S3_ENDPOINT": "nbg1.your-objectstorage.com",
        "PGBACKREST_REPO1_S3_REGION": "nbg1",
        "PGBACKREST_REPO1_S3_KEY": "AKIAEXAMPLEBACKUPKEY",
        "PGBACKREST_REPO1_S3_KEY_SECRET": "a-real-looking-backup-secret-value",
        "PGBACKREST_REPO1_CIPHER_PASS": "0123456789abcdef" * 4,
        "BACKUP_HEARTBEAT_URL": "https://ping.example/backup",
        "BACKUP_ARCHIVE_HEARTBEAT_URL": "https://ping.example/archive",
    }


def _write_deploy_box(
    root: Path, *, backup_enabled: str, backup_env: dict[str, str] | None
) -> None:
    """A checkout as deploy.sh sees it: both examples, .env, and .env.backup
    unless `backup_env` is None."""
    example = {**EXAMPLE, "BACKUP_ENABLED": "false", "BACKUP_ARCHIVE_QUEUE_MAX": "4GiB"}
    actual = {
        **_deployable_env(),
        "BACKUP_ENABLED": backup_enabled,
        "BACKUP_ARCHIVE_QUEUE_MAX": "4GiB",
    }
    (root / ".env.example").write_text(_dotenv(example))
    (root / ".env").write_text(_dotenv(actual))
    (root / ".env.backup.example").write_text(_dotenv(BACKUP_EXAMPLE))
    if backup_env is not None:
        (root / ".env.backup").write_text(_dotenv(backup_env))


def _dotenv(entries: dict[str, str]) -> str:
    return "".join(f"{key}={value}\n" for key, value in entries.items())


def _gate_output(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[int | str | None, str]:
    monkeypatch.chdir(root)
    try:
        main([])
    except SystemExit as raised:
        return raised.code, capsys.readouterr().out
    return 0, capsys.readouterr().out


def test_backup_off_ignores_a_missing_backup_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every box without continuous backup has no .env.backup at all."""
    _write_deploy_box(tmp_path, backup_enabled="false", backup_env=None)

    code, _ = _gate_output(tmp_path, monkeypatch, capsys)

    assert code == 0


def test_backup_on_requires_the_backup_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """compose loads .env.backup with required: false, so without the gate a
    box would start archiving with no bucket and no credentials."""
    _write_deploy_box(tmp_path, backup_enabled="true", backup_env=None)

    code, output = _gate_output(tmp_path, monkeypatch, capsys)

    assert code == 1
    assert output.startswith(".env.backup:")


def test_backup_on_with_a_filled_backup_file_passes_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_deploy_box(
        tmp_path, backup_enabled="yes", backup_env=_deployable_backup_env()
    )

    code, _ = _gate_output(tmp_path, monkeypatch, capsys)

    assert code == 0


def test_backup_on_reads_the_backup_file_as_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """parse_env would strip these quotes, while `docker run --env-file` keeps
    them in the bucket name."""
    backup_env = {**_deployable_backup_env(), "PGBACKREST_REPO1_S3_BUCKET": '"bucket"'}
    _write_deploy_box(tmp_path, backup_enabled="true", backup_env=backup_env)

    code, output = _gate_output(tmp_path, monkeypatch, capsys)

    assert code == 1
    assert "PGBACKREST_REPO1_S3_BUCKET contains a quote" in output


def test_deployable_backup_env_has_no_problems() -> None:
    assert collect_backup_problems(BACKUP_EXAMPLE, _deployable_backup_env()) == []


def test_missing_backup_keys_are_reported() -> None:
    actual = _deployable_backup_env()
    del actual["PGBACKREST_REPO1_S3_KEY"]

    assert collect_backup_problems(BACKUP_EXAMPLE, actual) == [
        ".env.backup: missing keys: PGBACKREST_REPO1_S3_KEY"
    ]


@pytest.mark.parametrize(
    "key",
    [
        "PGBACKREST_REPO1_S3_KEY",
        "PGBACKREST_REPO1_S3_KEY_SECRET",
        "PGBACKREST_REPO1_CIPHER_PASS",
    ],
)
def test_backup_on_refuses_placeholder_backup_secrets(key: str) -> None:
    shipped = parse_raw_env(REPO_ROOT / ".env.backup.example")
    actual = {**_deployable_backup_env(), key: shipped[key]}

    assert collect_backup_problems(shipped, actual) == [
        f".env.backup: {key} still holds the placeholder value from "
        ".env.backup.example"
    ]


@pytest.mark.parametrize(
    "key",
    [
        "PGBACKREST_REPO1_S3_KEY",
        "PGBACKREST_REPO1_S3_KEY_SECRET",
        "PGBACKREST_REPO1_CIPHER_PASS",
    ],
)
def test_a_backup_secret_equal_to_an_edited_example_is_refused(key: str) -> None:
    """A fork that drops the marker from its example must not turn the copied
    value into an accepted credential."""
    secret = _deployable_backup_env()[key]
    example = {**BACKUP_EXAMPLE, key: secret}

    assert collect_backup_problems(example, _deployable_backup_env()) == [
        f".env.backup: {key} still holds the placeholder value from "
        ".env.backup.example"
    ]


@pytest.mark.parametrize(
    "key",
    [
        "PGBACKREST_REPO1_S3_BUCKET",
        "PGBACKREST_REPO1_S3_ENDPOINT",
        "PGBACKREST_REPO1_S3_REGION",
    ],
)
def test_backup_on_requires_bucket_endpoint_and_region(key: str) -> None:
    actual = {**_deployable_backup_env(), key: ""}

    assert collect_backup_problems(BACKUP_EXAMPLE, actual) == [
        f".env.backup: {key} is empty"
    ]


@pytest.mark.parametrize("value", ["", "  "])
def test_backup_on_requires_a_queue_limit(value: str) -> None:
    """An empty value reaches pgBackRest as no queue limit, and an unreachable
    bucket then fills the disk with WAL until PostgreSQL stops."""
    example = {**EXAMPLE, "BACKUP_ENABLED": "false", "BACKUP_ARCHIVE_QUEUE_MAX": "4GiB"}
    actual = {
        **_deployable_env(),
        "BACKUP_ENABLED": "true",
        "BACKUP_ARCHIVE_QUEUE_MAX": value,
    }

    assert collect_problems(example, actual) == [
        "BACKUP_ARCHIVE_QUEUE_MAX is empty: BACKUP_ENABLED=true needs it set"
    ]


def test_backup_off_allows_an_empty_queue_limit() -> None:
    example = {**EXAMPLE, "BACKUP_ENABLED": "false", "BACKUP_ARCHIVE_QUEUE_MAX": "4GiB"}
    actual = {
        **_deployable_env(),
        "BACKUP_ENABLED": "false",
        "BACKUP_ARCHIVE_QUEUE_MAX": "",
    }

    assert collect_problems(example, actual) == []


def test_backup_on_accepts_empty_heartbeat_urls() -> None:
    actual = {
        **_deployable_backup_env(),
        "BACKUP_HEARTBEAT_URL": "",
        "BACKUP_ARCHIVE_HEARTBEAT_URL": "",
    }

    assert collect_backup_problems(BACKUP_EXAMPLE, actual) == []


def test_backup_secrets_never_appear_in_the_shared_example() -> None:
    """.env reaches the app, worker and scheduler; a pgBackRest credential
    there is one the application could leak."""
    example = parse_env(REPO_ROOT / ".env.example")

    assert [key for key in example if key.startswith("PGBACKREST_")] == []


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        ("", "BACKUP_ENABLED is empty: set true or false"),
        ("maybe", "BACKUP_ENABLED=maybe is not a boolean"),
    ],
)
def test_backup_enabled_must_be_a_boolean(value: str, problem: str) -> None:
    """archive-push and deploy.sh each read the flag on their own; a value that
    is neither true nor false would leave them to disagree."""
    example = {**EXAMPLE, "BACKUP_ENABLED": "false", "BACKUP_ARCHIVE_QUEUE_MAX": "4GiB"}
    actual = {
        **_deployable_env(),
        "BACKUP_ENABLED": value,
        "BACKUP_ARCHIVE_QUEUE_MAX": "4GiB",
    }

    assert collect_problems(example, actual) == [problem]


@pytest.mark.parametrize(
    "value",
    [
        pytest.param('"app-backups"', id="double-quote"),
        pytest.param("'app-backups'", id="single-quote"),
        pytest.param("app$backups", id="dollar"),
        pytest.param("app-backups # main", id="hash"),
    ],
)
def test_backup_values_refuse_quotes_dollars_and_hashes(value: str) -> None:
    actual = {**_deployable_backup_env(), "PGBACKREST_REPO1_S3_BUCKET": value}

    assert collect_backup_problems(BACKUP_EXAMPLE, actual) == [
        '.env.backup: PGBACKREST_REPO1_S3_BUCKET contains a quote, "$" or "#", '
        "which compose and docker read differently"
    ]


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("0123456789abcdef" * 2 + "0", id="hex-33"),
        pytest.param("A" * 32, id="letters-32"),
    ],
)
def test_the_cipher_pass_accepts_long_alphanumeric_values(value: str) -> None:
    actual = {**_deployable_backup_env(), "PGBACKREST_REPO1_CIPHER_PASS": value}

    assert collect_backup_problems(BACKUP_EXAMPLE, actual) == []


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("a" * 31, id="short"),
        pytest.param("0123456789abcdef0123456789abcde-", id="punctuation"),
        pytest.param("correct horse battery staple again", id="spaces"),
    ],
)
def test_the_cipher_pass_is_long_and_alphanumeric(value: str) -> None:
    actual = {**_deployable_backup_env(), "PGBACKREST_REPO1_CIPHER_PASS": value}

    assert collect_backup_problems(BACKUP_EXAMPLE, actual) == [
        ".env.backup: PGBACKREST_REPO1_CIPHER_PASS must be at least 32 letters and "
        "digits (openssl rand -hex 32)"
    ]


@pytest.mark.parametrize(
    ("value", "printed"),
    [("yes", "true\n"), ("off", "false\n"), ("TRUE", "true\n")],
)
def test_enabled_flag_prints_the_parsed_boolean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str,
    printed: str,
) -> None:
    (tmp_path / ".env").write_text(f"BACKUP_ENABLED={value}\n")
    monkeypatch.chdir(tmp_path)

    main(["--enabled", "BACKUP_ENABLED"])

    assert capsys.readouterr().out == printed


@pytest.mark.parametrize("content", ["BACKUP_ENABLED=maybe\n", "DEBUG=false\n"])
def test_enabled_flag_fails_on_a_value_that_is_not_a_boolean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    """deploy.sh would otherwise read an unparsed value as "off" and skip the
    backup steps on a box that asked for them."""
    (tmp_path / ".env").write_text(content)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as raised:
        main(["--enabled", "BACKUP_ENABLED"])

    assert raised.value.code == 1


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("https://nbg1.your-objectstorage.com", id="scheme"),
        pytest.param("nbg1.your-objectstorage.com/app-backups", id="path"),
    ],
)
def test_backup_endpoint_must_be_a_bare_host(value: str) -> None:
    """A scheme or a path passes as a non-empty value and fails only at
    stanza-create, after PostgreSQL restarted with archiving on."""
    actual = {**_deployable_backup_env(), "PGBACKREST_REPO1_S3_ENDPOINT": value}

    assert collect_backup_problems(BACKUP_EXAMPLE, actual) == [
        ".env.backup: PGBACKREST_REPO1_S3_ENDPOINT must be a host without scheme "
        "or path (nbg1.your-objectstorage.com)"
    ]
