"""Official access instructions and local, non-echoing credential entry."""

import getpass
import sys
import warnings
from pathlib import Path

from dotenv import dotenv_values, set_key

from .config import Settings
from .errors import DataError

SOURCE_ACCESS = {
    "ostrovok": {
        "name": "Островок B2B (необязательный адаптер)",
        "variables": ("OSTROVOK_KEY_ID", "OSTROVOK_API_KEY"),
        "portal": "https://b2b.ostrovok.ru/",
        "instructions": "https://help.b2b.ostrovok.ru/ru/articles/9330513",
        "note": "Ключи выдаёт партнёрский куратор. Sandbox не подтверждает доступ к реальным ценам.",
    },
    "rasp": {
        "name": "Яндекс Расписания",
        "variables": ("YANDEX_RASP_API_KEY",),
        "portal": "https://developer.tech.yandex.ru/",
        "instructions": "https://yandex.ru/dev/rasp/doc/ru/concepts/access",
        "note": "Подключите бесплатный API Расписаний; затем выполните import-stations.",
    },
    "weather": {
        "name": "Яндекс Погода",
        "variables": ("YANDEX_WEATHER_API_KEY",),
        "portal": "https://yandex.ru/pogoda/b2b/console",
        "instructions": "https://yandex.ru/dev/weather/doc/ru/concepts/forecast-rest",
        "note": "Адаптеру нужен доступ к REST v2 forecast, тариф Оптимальный (REST). Уточните доступность демо в кабинете.",
    },
    "2gis": {
        "name": "2ГИС Routing API",
        "variables": ("DGIS_API_KEY",),
        "portal": "https://platform.2gis.ru/",
        "instructions": "https://docs.2gis.com/platform-manager/subscription/keys",
        "note": "Демо-ключ выдаётся на месяц; демо Routing API ограничено маршрутами до 50 км.",
    },
}


def configure_keys(env_file, source="all", *, secret_input=None, output=print):
    """Prompt only in a real terminal. Never accept a secret in command-line arguments."""
    if secret_input is None:
        if not sys.stdin.isatty():
            raise DataError(
                "INVALID_ARGUMENT", "Для скрытого ввода ключей откройте START.cmd или обычную консоль"
            )
        secret_input = getpass.getpass
    path = Path(env_file)
    existing = dotenv_values(path, interpolate=False) if path.is_file() else {}
    if source == "all":
        use_ostrovok = Settings.from_env(path).selected_hotel_provider == "ostrovok"
        selected = [item for key, item in SOURCE_ACCESS.items() if key != "ostrovok" or use_ostrovok]
        if not use_ostrovok:
            output("Для отелей выбран Bnovo: партнёрские ключи не требуются.")
    else:
        selected = [SOURCE_ACCESS[source]]
    updates = {}
    try:
        with warnings.catch_warnings():
            # getpass normally falls back to echoed input when no terminal is available.
            warnings.simplefilter("error", getpass.GetPassWarning)
            for service in selected:
                output(f"\n{service['name']}: {service['portal']}")
                output(service["note"])
                for variable in service["variables"]:
                    state = "уже сохранён" if existing.get(variable) else "пока не задан"
                    value = secret_input(f"{variable} ({state}; Enter — оставить): ")
                    if not value:
                        continue
                    if any(ord(char) < 32 or ord(char) == 127 for char in value) or value != value.strip():
                        raise DataError(
                            "INVALID_ARGUMENT", "В ключе есть пробелы по краям или управляющие символы"
                        )
                    updates[variable] = value
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        raise DataError(
            "INVALID_ARGUMENT", "Ввод отменён или скрытый ввод недоступен; ключи не изменены"
        ) from None
    if updates:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("# Local provider credentials. Never commit this file.\n", encoding="utf-8")
        for variable, value in updates.items():
            set_key(path, variable, value, quote_mode="always")
    return {"env_file": str(path), "updated_variables": list(updates), "live_verified": False}
