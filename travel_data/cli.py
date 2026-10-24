import argparse
import asyncio
import gzip
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from .access import SOURCE_ACCESS, configure_keys
from .client import TravelDataClient, call, make_request
from .config import Settings
from .contracts import TOOLS, VERSION
from .demo import ARGUMENTS
from .errors import DataError
from .utils import utc_iso


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def doctor(settings):
    return {
        "schema_version": VERSION,
        "python": sys.version.split()[0],
        "contracts": "reconstructed_from_docx_pending_original_archive",
        "sources": {
            "ostrovok_b2b": {
                "configured": bool(settings.ostrovok_key_id and settings.ostrovok_api_key),
                "live_verified": False,
            },
            "yandex_rasp": {
                "configured": bool(settings.yandex_rasp_api_key),
                "city_index_exists": Path(settings.yandex_cities_file).is_file(),
                "live_verified": False,
            },
            "yandex_weather": {"configured": bool(settings.yandex_weather_api_key), "live_verified": False},
            "2gis_routing": {"configured": bool(settings.dgis_api_key), "live_verified": False},
        },
        "note": "doctor проверяет конфигурацию; реальную проверку выполняет live-check",
    }


def import_stations(settings, output, country_name="Россия", country_code="RU", input_file=None):
    if input_file:
        payload = json.loads(Path(input_file).read_text(encoding="utf-8"))
    else:
        if not settings.yandex_rasp_api_key:
            raise DataError("ACCESS_NOT_CONFIGURED", "Не задан YANDEX_RASP_API_KEY")
        # A bulk import is intentionally separate from tool calls and their 15 second budget.
        with httpx.Client(timeout=90, trust_env=False, follow_redirects=False) as client:
            response = client.get(
                settings.yandex_rasp_base_url + "stations_list/",
                headers={"Authorization": settings.yandex_rasp_api_key},
                params={"format": "json", "lang": "ru_RU"},
            )
            if response.status_code in (401, 403):
                raise DataError("SOURCE_AUTH_FAILED", "Яндекс Расписания отклонили ключ")
            if response.status_code == 429:
                raise DataError("SOURCE_RATE_LIMITED", "Лимит Яндекс Расписаний", True)
            if response.status_code != 200:
                raise DataError("SOURCE_UNAVAILABLE", "Не удалось загрузить справочник станций", True)
            payload = response.json()
    rows = []
    for country in payload["countries"]:
        if country["title"] != country_name:
            continue
        for region in country.get("regions", []):
            for city in region.get("settlements", []):
                code = city.get("codes", {}).get("yandex_code")
                if city.get("title") and code:
                    rows.append(
                        {
                            "city": city["title"],
                            "country_code": country_code,
                            "yandex_code": code,
                            "region": region.get("title"),
                        }
                    )
    if not rows:
        raise DataError("SOURCE_SCHEMA_CHANGED", "В справочнике не найдены города выбранной страны")
    write_json(output, rows)
    return {
        "imported_cities": len(rows),
        "output": str(output),
        "source_name": "yandex_rasp",
        "imported_at": utc_iso(),
        "is_fixture": False,
    }


def import_hotels(input_file, output):
    source = Path(input_file)
    opener = gzip.open if source.suffix == ".gz" else open
    rows = {}
    with opener(source, "rt", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            hotel = json.loads(line)
            info = {key: hotel.get(key) for key in ("name", "address", "latitude", "longitude")}
            if not info["name"]:
                continue
            for identity in (hotel.get("id"), hotel.get("hid")):
                if identity:
                    rows[str(identity)] = info
    write_json(output, rows)
    return {"imported_ids": len(rows), "output": str(output), "is_fixture": False}


async def live_check(settings, output):
    today = datetime.now(UTC).date()
    requests = json.loads(json.dumps(ARGUMENTS))
    check_in = today + timedelta(days=14)
    requests["search_hotels"]["check_in"] = check_in.isoformat()
    requests["search_hotels"]["check_out"] = (check_in + timedelta(days=7)).isoformat()
    requests["search_transport"]["departure_date"] = check_in.isoformat()
    requests["get_weather"]["start_date"] = today.isoformat()
    requests["get_weather"]["end_date"] = (today + timedelta(days=2)).isoformat()
    results = []
    async with TravelDataClient(settings) as client:
        for tool in TOOLS:
            request = make_request(tool, requests[tool], f"live_check_{tool}")
            response = await client.execute(request)
            results.append(
                {
                    "request": request,
                    "response": response,
                    "verified": response["status"] in ("ok", "empty", "partial"),
                }
            )
    report = {
        "checked_at": utc_iso(),
        "is_fixture": False,
        "results": results,
        "all_verified": all(result["verified"] for result in results),
    }
    if output:
        write_json(output, report)
    return report


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Данные для трека О!дин запрос")
    parser.add_argument("--env-file", default=".env")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Проверить конфигурацию без раскрытия ключей")
    configure = sub.add_parser("configure", help="Сохранить свои ключи в .env со скрытым вводом")
    configure.add_argument("--source", choices=("all", *SOURCE_ACCESS), default="all")
    request_parser = sub.add_parser("call", help="Выполнить JSON запрос из файла или stdin")
    request_parser.add_argument("request", help="Путь к JSON или - для stdin")
    request_parser.add_argument("--mode", choices=("live", "fixture"), default="live")
    demo_parser = sub.add_parser("demo", help="Запустить фиксированные вымышленные примеры")
    demo_parser.add_argument("--tool", choices=TOOLS)
    check = sub.add_parser("live-check", help="Реально вызвать каждый подключённый источник")
    check.add_argument("--output", default=None)
    stations = sub.add_parser("import-stations", help="Отдельно импортировать справочник городов")
    stations.add_argument("--input", default=None, help="Локальная выгрузка вместо скачивания")
    stations.add_argument("--output", default=None)
    stations.add_argument("--country-name", default="Россия")
    stations.add_argument("--country-code", default="RU")
    hotels = sub.add_parser("import-hotels", help="Импортировать разрешённую JSONL/JSONL.GZ выгрузку отелей")
    hotels.add_argument("input")
    hotels.add_argument("--output", default=None)
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env(args.env_file)
        if args.command == "doctor":
            result = doctor(settings)
        elif args.command == "configure":
            result = configure_keys(args.env_file, args.source)
            # A saved key is configuration, not evidence of successful API access.
        elif args.command == "call":
            raw = (
                sys.stdin.read()
                if args.request == "-"
                else Path(args.request).read_text(encoding="utf-8-sig")
            )
            result = call(json.loads(raw), settings=settings, mode=args.mode)
        elif args.command == "demo":
            result = [
                call(make_request(tool, ARGUMENTS[tool], f"demo_{tool}"), settings=settings, mode="fixture")
                for tool in ([args.tool] if args.tool else TOOLS)
            ]
        elif args.command == "live-check":
            result = asyncio.run(live_check(settings, args.output))
        elif args.command == "import-stations":
            result = import_stations(
                settings,
                args.output or settings.yandex_cities_file,
                args.country_name,
                args.country_code,
                args.input,
            )
        else:
            result = import_hotels(args.input, args.output or settings.ostrovok_hotels_file)
        print_json(result)
        if args.command == "live-check":
            return 0 if result["all_verified"] else 2
        return 2 if isinstance(result, dict) and result.get("status") == "error" else 0
    except DataError as error:
        print_json({"error": error.as_dict()})
        return 2
    except (ValueError, KeyError, OSError, httpx.HTTPError, TypeError, AttributeError):
        print_json(
            {
                "error": {
                    "code": "INVALID_ARGUMENT",
                    "message": "Проверьте JSON, пути, настройки и сеть",
                    "retryable": False,
                    "field": None,
                }
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
