"""Shared test setup: every test runs against the example config, never a real kayako.toml."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kayako_filer as kf  # noqa: E402


@pytest.fixture(autouse=True)
def example_config_is_the_default(monkeypatch):
    monkeypatch.setattr(kf, "DEFAULT_CONFIG", kf.SCRIPT_DIR / "kayako.example.toml")
