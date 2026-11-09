"""Settings read from .env, the phone link, and the setup files people get in the zip."""
from pathlib import Path

from loophole import config
from loophole import menubar as mb

ROOT = Path(__file__).resolve().parent.parent


def test_env_ints(monkeypatch):
    monkeypatch.setenv("X_LENS", "15, 45,120")
    assert config._env_ints("X_LENS", (25,)) == (15, 45, 120)
    monkeypatch.setenv("X_LENS", "")
    assert config._env_ints("X_LENS", (25, 50)) == (25, 50)
    monkeypatch.setenv("X_LENS", "ten")
    assert config._env_ints("X_LENS", (5,)) == (5,)
    monkeypatch.setenv("X_LENS", "0,-3")
    assert config._env_ints("X_LENS", (5,)) == (5,)


def test_phone_link():
    assert mb.phone_link("loophole-abc") == "https://ntfy.sh/loophole-abc"


def test_env_example_has_every_setting_and_no_key():
    text = (ROOT / ".env.example").read_text()
    for key in ("NTFY_TOPIC=", "LOOPHOLE_USER=", "LOOPHOLE_SESSION_LENGTHS=",
                "LOOPHOLE_BREAK_MINUTES=", "UNBOXAI_API_KEY=", "LOOPHOLE_CATALOG_ID="):
        assert key in text
    # The template ships an empty key and an empty topic: setup.sh fills the topic.
    assert "\nUNBOXAI_API_KEY=\n" in text
    assert "\nNTFY_TOPIC=\n" in text


def test_setup_scripts_exist():
    for name in ("setup.sh", "start.sh", "README.md", "docs/phone-setup.md"):
        assert (ROOT / name).exists(), name
