from ..errors import DataError
from ..models import Candidate, ProviderResult, warning
from ..utils import number, stable_id


class DgisRoute:
    name = "2gis_routing"

    def __init__(self, settings, network):
        self.settings, self.network = settings, network

    async def fetch(self, args, deadline):
        if args["departure_at"] is not None:
            raise DataError(
                "UNSUPPORTED_REQUEST",
                "Этот адаптер не поддерживает маршрут на заданное время",
                field="departure_at",
            )
        if not self.settings.dgis_api_key:
            raise DataError("ACCESS_NOT_CONFIGURED", "Не задан DGIS_API_KEY")
        payload = await self.network.json(
            "POST",
            self.settings.dgis_routing_url,
            deadline=deadline,
            params={"key": self.settings.dgis_api_key},
            json={
                "points": [{**args["origin"], "type": "stop"}, {**args["destination"], "type": "stop"}],
                "transport": "driving",
                "route_mode": "fastest",
                "traffic_mode": "jam",
                "locale": "ru",
            },
        )
        if "status" not in payload:
            raise DataError("SOURCE_SCHEMA_CHANGED", "В ответе 2ГИС отсутствует status")
        if payload.get("status") != "OK":
            raise DataError("SOURCE_UNAVAILABLE", "2ГИС не вернул успешный результат")
        if not isinstance(payload.get("result"), list):
            raise DataError("SOURCE_SCHEMA_CHANGED", "В ответе 2ГИС отсутствует result")
        if not payload["result"]:
            return ProviderResult()
        try:
            row = payload["result"][0]
            item = {
                "id": stable_id("2gis_route", args, row.get("id")),
                "origin": args["origin"],
                "destination": args["destination"],
                "distance_m": number(row["total_distance"], minimum=0, integer=True),
                "duration_s": number(row["total_duration"], minimum=0, integer=True),
                # Requested 'jam' alone does not prove which traffic data the server actually used.
                "traffic_mode": "unknown",
                "departure_at": None,
                "source_url": "https://2gis.ru/",
            }
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            raise DataError("SOURCE_SCHEMA_CHANGED", "Изменилась структура маршрута 2ГИС") from None
        return ProviderResult(
            [Candidate(item)],
            has_more=len(payload["result"]) > 1,
            warnings=[
                warning(
                    "TRAFFIC_UNCONFIRMED", "Тип фактически использованных данных о пробках не подтверждён"
                )
            ],
        )
