from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _patterns() -> list[str]:
    lines = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def test_every_env_file_stays_out_of_the_image() -> None:
    """The Dockerfile copies the whole context, so listing env files one by one
    let any other spelling - .env.prod, .env.backup - ship its secrets in a
    layer of an image pushed to the registry."""
    patterns = _patterns()

    assert ".env*" in patterns
    assert not [pattern for pattern in patterns if pattern.startswith("!.env")]
