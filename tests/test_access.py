import json

import pytest
from dotenv import dotenv_values

from travel_data.access import configure_keys
from travel_data.config import Settings
from travel_data.errors import DataError


def test_configure_preserves_existing_settings_and_roundtrips_literal_secret(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# Keep this comment\nCACHE_ALLOWED=false\nDGIS_API_KEY='existing'\n", encoding="utf-8")
    messages = []
    secret = "key-with-${LITERAL}-and-'quote"
    result = configure_keys(env, "weather", secret_input=lambda _: secret, output=messages.append)
    values = dotenv_values(env, interpolate=False)
    assert values["YANDEX_WEATHER_API_KEY"] == secret
    monkeypatch.delenv("YANDEX_WEATHER_API_KEY", raising=False)
    assert Settings.from_env(env).yandex_weather_api_key == secret
    assert values["DGIS_API_KEY"] == "existing" and values["CACHE_ALLOWED"] == "false"
    assert "# Keep this comment" in env.read_text(encoding="utf-8")
    assert secret not in json.dumps(result) + "".join(messages)
    assert result["updated_variables"] == ["YANDEX_WEATHER_API_KEY"]
    assert result["live_verified"] is False


def test_blank_input_does_not_erase_existing_key(tmp_path):
    env = tmp_path / ".env"
    content = "DGIS_API_KEY='existing'\n"
    env.write_text(content, encoding="utf-8")
    result = configure_keys(env, "2gis", secret_input=lambda _: "", output=lambda _: None)
    assert result["updated_variables"] == []
    assert env.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("failure", [EOFError(), "bad\nkey"])
def test_cancelled_or_invalid_input_leaves_all_keys_unchanged(tmp_path, failure):
    env = tmp_path / ".env"
    content = "OSTROVOK_API_KEY='old'\n"
    env.write_text(content, encoding="utf-8")
    answers = iter(["new-id", failure])

    def secret_input(_):
        value = next(answers)
        if isinstance(value, Exception):
            raise value
        return value

    with pytest.raises(DataError):
        configure_keys(env, "ostrovok", secret_input=secret_input, output=lambda _: None)
    assert env.read_text(encoding="utf-8") == content


def test_configure_refuses_echoed_piped_input(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(DataError) as error:
        configure_keys(tmp_path / ".env")
    assert error.value.code == "INVALID_ARGUMENT"
    assert not (tmp_path / ".env").exists()


def test_cli_passes_selected_source_and_env_path(monkeypatch, tmp_path, capsys):
    from travel_data.cli import main

    path = tmp_path / ".env"
    received = []

    def fake_configure(env_file, source):
        received.append((env_file, source))
        return {"updated_variables": [], "live_verified": False}

    monkeypatch.setattr("travel_data.cli.configure_keys", fake_configure)
    assert main(["--env-file", str(path), "configure", "--source", "rasp"]) == 0
    assert received == [(str(path), "rasp")]
    assert json.loads(capsys.readouterr().out)["live_verified"] is False
