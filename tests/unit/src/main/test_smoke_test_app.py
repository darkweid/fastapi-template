import importlib


def test_main_web_module_imports_without_errors() -> None:
    module = importlib.import_module("src.main.web")

    assert hasattr(module, "app")
    assert module.app is not None
