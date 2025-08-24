from pathlib import Path


def check_env_file() -> None:
    """
    Checks if all required keys from .env.example are present in .env.
    """
    try:
        required_keys = {
            line.split("=")[0]
            for line in Path(".env.example").read_text().strip().splitlines()
            if line and not line.startswith("#")
        }

        actual_keys = {
            line.split("=")[0]
            for line in Path(".env").read_text().strip().splitlines()
            if line and not line.startswith("#")
        }

        missing_keys = required_keys - actual_keys
        if missing_keys:
            print(f"Missing keys in .env: {', '.join(missing_keys)}")
            raise SystemExit(1)
        else:
            print("All required keys are present in .env.")
    except FileNotFoundError as e:
        print(f"File not found: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    check_env_file()
