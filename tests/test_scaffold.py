"""Scaffold tests (K1): config, URL joining, credentials, helpers. No network."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kayako_filer as kf

BASE = "https://helpdesk.example.test/support/staff/index.php?"
LOGIN = BASE + "/Core/Default/Login"


def _cfg(**overrides):
    cfg = {"server": {"base_url": BASE, "verify_tls": True}}
    cfg["server"].update(overrides)
    return cfg


def test_url_join():
    assert kf.url(_cfg(), "/Core/Default/Login") == LOGIN


def test_url_join_without_leading_slash():
    assert kf.url(_cfg(), "Core/Default/Login") == LOGIN


def test_load_config_missing_file_exits_2(tmp_path):
    with pytest.raises(SystemExit) as exc:
        kf.load_config(tmp_path / "nope.toml")
    assert exc.value.code == 2


def test_load_config_missing_section_exits_2(tmp_path):
    p = tmp_path / "kayako.toml"
    p.write_text("[other]\nx = 1\n")
    with pytest.raises(SystemExit) as exc:
        kf.load_config(p)
    assert exc.value.code == 2


def test_load_config_ok():
    cfg = kf.load_config(kf.SCRIPT_DIR / "kayako.example.toml")
    assert cfg["server"]["base_url"] == BASE
    assert cfg["send"]["allow_to"] == []
    assert cfg["server"].get("verify_tls") is True


def test_load_creds_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KAYAKO_USER", "env-user")
    monkeypatch.setenv("KAYAKO_PASS", "env-pass")
    monkeypatch.setattr(kf, "SCRIPT_DIR", tmp_path)
    (tmp_path / ".env").write_text("KAYAKO_USER=dot\nKAYAKO_PASS=dot\n")
    assert kf.load_creds() == ("env-user", "env-pass")


def test_load_creds_dotenv_then_creds_file(monkeypatch, tmp_path):
    monkeypatch.delenv("KAYAKO_USER", raising=False)
    monkeypatch.delenv("KAYAKO_PASS", raising=False)
    monkeypatch.setattr(kf, "SCRIPT_DIR", tmp_path)
    (tmp_path / ".env").write_text("KAYAKO_USER=dot-user\nKAYAKO_PASS=dot-pass\n")
    home = tmp_path / "home"
    (home / ".config" / "kayako").mkdir(parents=True)
    (home / ".config" / "kayako" / "creds").write_text("KAYAKO_USER=file\nKAYAKO_PASS=file\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert kf.load_creds() == ("dot-user", "dot-pass")
    (tmp_path / ".env").unlink()
    (home / ".config" / "kayako" / "creds").chmod(0o600)
    assert kf.load_creds() == ("file", "file")


def test_load_creds_exits_when_none(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("KAYAKO_USER", raising=False)
    monkeypatch.delenv("KAYAKO_PASS", raising=False)
    monkeypatch.setattr(kf, "SCRIPT_DIR", tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    with pytest.raises(SystemExit) as exc:
        kf.load_creds()
    assert exc.value.code == 2
    assert "KAYAKO_USER" in capsys.readouterr().err

    creds_dir = home / ".config" / "kayako"
    creds_dir.mkdir(parents=True)
    creds = creds_dir / "creds"
    secret = "s3cret-leak-probe-9f8e7d6c"
    creds.write_text(f"KAYAKO_USER=probe-user\nKAYAKO_PASS={secret}\n")
    creds.chmod(0o644)
    assert kf.load_creds() == ("probe-user", secret)
    out = capsys.readouterr()
    assert secret not in out.out
    assert secret not in out.err
    assert "expected 0o600" in out.err


class _FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, headers=None, **kwargs):
        self.calls.append(("GET", url, headers, kwargs))
        return ("GET", url, headers)

    def post(self, url, data=None, headers=None, **kwargs):
        self.calls.append(("POST", url, data, headers, kwargs))
        return ("POST", url, data, headers)


def test_get_post_headers():
    cfg = _cfg()
    fake = _FakeSession()
    kf.get(fake, cfg, "/Core/Default/Login")
    kf.get(fake, cfg, "/Core/Default/Login", xhr=True)
    kf.post(fake, cfg, "/Core/Default/Login", data={"a": "b"}, xhr=True)
    plain, xhr_get, xhr_post = fake.calls
    assert plain[1] == LOGIN
    assert "X-Requested-With" not in plain[2]
    assert xhr_get[2].get("X-Requested-With") == "XMLHttpRequest"
    assert xhr_post[3].get("X-Requested-With") == "XMLHttpRequest"
    assert "User-Agent" in xhr_get[2]
    assert plain[3].get("verify") is True


def test_selftest_passes(monkeypatch):
    monkeypatch.delenv("KAYAKO_USER", raising=False)
    monkeypatch.delenv("KAYAKO_PASS", raising=False)
    assert kf.run_selftest() is True
    assert kf.main(["selftest"]) == 0


def test_help_works(capsys):
    with pytest.raises(SystemExit) as exc:
        kf.build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    assert kf.main([]) == 2
    out = capsys.readouterr()
    assert "selftest" in out.out


def test_example_config_has_no_secrets():
    text = (kf.SCRIPT_DIR / "kayako.example.toml").read_text().lower()
    assert "pass" not in text
    assert "secret" not in text
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s and "[" not in s:
            assert not any(ch.isdigit() and len(s.split("=")[1]) > 20 for ch in s) or "example.test" in s
    assert "staff_id" not in text and "staffid" not in text
    assert os.environ.get("KAYAKO_PASS", "") not in text or not os.environ.get("KAYAKO_PASS")
