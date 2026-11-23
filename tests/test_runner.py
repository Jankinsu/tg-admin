import importlib.util
from pathlib import Path


def load_runner():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("runner", root / "runner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_simple_command():
    result = load_runner().run("printf hello")
    assert result["code"] == 0
    assert result["stdout"] == "hello"
    assert result["stderr"] == ""


def test_runner_invalid_command():
    result = load_runner().run("echo a\necho b")
    assert result["code"] == 2


def test_runner_output_bound():
    result = load_runner().run("yes x")
    assert result["truncated"]
    assert len(result["stdout"].encode()) <= 24576
