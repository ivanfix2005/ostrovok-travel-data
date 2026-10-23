import json

from travel_data.cli import import_hotels, import_stations, main
from travel_data.config import Settings


def test_cli_demo_is_explicit(capsys):
    assert main(["demo"]) == 0
    responses = json.loads(capsys.readouterr().out)
    assert len(responses) == 4 and all(r["meta"]["is_fixture"] for r in responses)


def test_doctor_does_not_reveal_secret(monkeypatch, capsys):
    monkeypatch.setenv("YANDEX_WEATHER_API_KEY", "secret-never-display")
    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "secret-never-display" not in output
    assert json.loads(output)["sources"]["yandex_weather"]["configured"]


def test_import_stations_keeps_ambiguous_cities(tmp_path):
    source = tmp_path / "stations.json"
    source.write_text(
        json.dumps(
            {
                "countries": [
                    {
                        "title": "Россия",
                        "regions": [
                            {
                                "title": "Регион 1",
                                "settlements": [{"title": "Советск", "codes": {"yandex_code": "c1"}}],
                            },
                            {
                                "title": "Регион 2",
                                "settlements": [{"title": "Советск", "codes": {"yandex_code": "c2"}}],
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "cities.json"
    result = import_stations(Settings(), output, input_file=source)
    assert result["imported_cities"] == 2
    assert len(json.loads(output.read_text(encoding="utf-8"))) == 2


def test_import_hotels_keeps_only_needed_fields(tmp_path):
    source = tmp_path / "hotels.jsonl"
    source.write_text(
        json.dumps(
            {
                "id": "test",
                "hid": 123,
                "name": "Тест",
                "address": "Адрес",
                "latitude": 1,
                "longitude": 2,
                "images": ["huge"],
                "description": "unused",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "hotels.json"
    import_hotels(source, output)
    catalogue = json.loads(output.read_text(encoding="utf-8"))
    assert "123" in catalogue and "test" in catalogue
    assert "images" not in catalogue["test"] and "description" not in catalogue["test"]


def test_settings_repr_does_not_leak_secrets():
    settings = Settings(ostrovok_api_key="secret", yandex_weather_api_key="secret", dgis_api_key="secret")
    assert "secret" not in repr(settings)
