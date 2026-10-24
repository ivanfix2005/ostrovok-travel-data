import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    ostrovok_key_id: str = field(default="", repr=False)
    ostrovok_api_key: str = field(default="", repr=False)
    yandex_rasp_api_key: str = field(default="", repr=False)
    yandex_weather_api_key: str = field(default="", repr=False)
    dgis_api_key: str = field(default="", repr=False)
    ostrovok_base_url: str = "https://api.ostrovok.ru/api/b2b/v3/"
    yandex_rasp_base_url: str = "https://api.rasp.yandex-net.ru/v3.0/"
    yandex_weather_url: str = "https://api.weather.yandex.ru/v2/forecast"
    dgis_routing_url: str = "https://routing.api.2gis.com/routing/7.0.0/global"
    yandex_cities_file: str = "data/yandex-cities.json"
    ostrovok_hotels_file: str = "data/ostrovok-hotels.json"
    cache_allowed: bool = False
    cache_ttl_seconds: float = 0
    time_budget_seconds: float = 15
    request_timeout_seconds: float = 6
    max_response_bytes: int = 32000

    def __post_init__(self):
        if not 0 < self.time_budget_seconds <= 15:
            raise ValueError("time_budget_seconds must be > 0 and <= 15")
        if not 0 < self.request_timeout_seconds <= 15:
            raise ValueError("request_timeout_seconds must be > 0 and <= 15")
        if self.cache_ttl_seconds < 0 or self.max_response_bytes < 4096:
            raise ValueError("Invalid cache TTL or response size")
        for value in (
            self.ostrovok_base_url,
            self.yandex_rasp_base_url,
            self.yandex_weather_url,
            self.dgis_routing_url,
        ):
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Provider URL must be HTTPS without credentials")
            if parsed.query or parsed.fragment:
                raise ValueError("Provider URL must not include query parameters or fragments")

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env"):
        from dotenv import dotenv_values

        env = {**(dotenv_values(env_file, interpolate=False) if env_file else {}), **os.environ}
        names = (
            "ostrovok_key_id",
            "ostrovok_api_key",
            "yandex_rasp_api_key",
            "yandex_weather_api_key",
            "dgis_api_key",
            "ostrovok_base_url",
            "yandex_rasp_base_url",
            "yandex_weather_url",
            "dgis_routing_url",
            "yandex_cities_file",
            "ostrovok_hotels_file",
        )
        values = {name: env[name.upper()] for name in names if env.get(name.upper())}
        values["cache_allowed"] = str(env.get("CACHE_ALLOWED", "false")).lower() == "true"
        values["cache_ttl_seconds"] = float(env.get("CACHE_TTL_SECONDS") or 0)
        return cls(**values)
