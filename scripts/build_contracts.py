"""Generate reviewable backend handoff files from a single source of truth."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from travel_data.cli import write_json
from travel_data.client import call, make_request
from travel_data.config import Settings
from travel_data.contracts import ARGUMENTS as SCHEMAS
from travel_data.contracts import REQUEST_SCHEMAS, RESPONSE_SCHEMAS, TOOLS
from travel_data.demo import ARGUMENTS

ROOT = Path(__file__).resolve().parents[1]
DESCRIPTIONS = {
    "search_hotels": "Найти предложения одного номера на точные даты и состав гостей. Цена в копейках за всю группу и срок.",
    "search_transport": "Найти транспорт в одну сторону. Обратный путь вызывается отдельно. Цена на группу может быть неизвестна.",
    "get_weather": "Получить доступный прогноз по координатам. Начало включено, конец исключён, максимум 16 дней.",
    "get_route": "Получить дорожное расстояние в метрах и время на автомобиле в секундах. Не рассчитывает цену трансфера.",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    def publish(path, value):
        if args.check:
            if json.loads(path.read_text(encoding="utf-8")) != value:
                raise RuntimeError(f"Export differs: {path.name}")
        else:
            write_json(path, value)

    tools = []
    for tool in TOOLS:
        publish(ROOT / "schemas" / f"{tool}.request.schema.json", REQUEST_SCHEMAS[tool])
        publish(ROOT / "schemas" / f"{tool}.response.schema.json", RESPONSE_SCHEMAS[tool])
        request = make_request(tool, ARGUMENTS[tool], f"example_{tool}")
        response = call(request, settings=Settings(), mode="fixture")
        if response["status"] == "error":
            raise RuntimeError(f"Fixture failed for {tool}: {response['error']['code']}")
        publish(ROOT / "examples" / f"{tool}.request.json", request)
        publish(ROOT / "examples" / f"{tool}.response.json", response)
        tools.append({"name": tool, "description": DESCRIPTIONS[tool], "input_schema": SCHEMAS[tool]})
    publish(ROOT / "tools.json", tools)
    empty = make_request(
        "search_hotels",
        {**ARGUMENTS["search_hotels"], "location": {"city": "Казань", "country_code": "RU"}},
        "example_empty",
    )
    publish(ROOT / "examples" / "empty.request.json", empty)
    publish(ROOT / "examples" / "empty.response.json", call(empty, settings=Settings(), mode="fixture"))
    invalid = make_request("search_hotels", {**ARGUMENTS["search_hotels"], "limit": 20}, "example_invalid")
    publish(ROOT / "examples" / "error.request.json", invalid)
    publish(ROOT / "examples" / "error.response.json", call(invalid, settings=Settings(), mode="fixture"))
    print("Checked exports" if args.check else "Generated 8 schemas, tools.json and 6 request/response pairs")


if __name__ == "__main__":
    main()
