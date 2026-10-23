from datetime import UTC, date, datetime

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from .contracts import REQUEST_SCHEMAS, RESPONSE_SCHEMAS
from .errors import DataError
from .utils import ensure_finite

_CHECKER = FormatChecker()
REQUEST_VALIDATORS = {k: Draft202012Validator(v, format_checker=_CHECKER) for k, v in REQUEST_SCHEMAS.items()}
RESPONSE_VALIDATORS = {
    k: Draft202012Validator(v, format_checker=_CHECKER) for k, v in RESPONSE_SCHEMAS.items()
}


def validate_request(request, now, *, fixture=False):
    if not isinstance(request, dict) or request.get("tool") not in REQUEST_VALIDATORS:
        raise DataError("INVALID_ARGUMENT", "Неизвестный инструмент", field="tool")
    try:
        ensure_finite(request)
    except ValueError:
        raise DataError("INVALID_ARGUMENT", "NaN и Infinity запрещены") from None
    supplied = request.get("arguments")
    if isinstance(supplied, dict):
        for key, allowed in (("transport_type", ("plane", "train", "bus")), ("mode", ("car",))):
            if key in supplied and isinstance(supplied[key], str) and supplied[key] not in allowed:
                raise DataError(
                    "UNSUPPORTED_REQUEST", "Этот способ передвижения не поддерживается", field=key
                )
    error = next(REQUEST_VALIDATORS[request["tool"]].iter_errors(request), None)
    if error:
        # Never include user values, credentials or jsonschema's full exception text.
        path = ".".join(str(part) for part in error.absolute_path)
        raise DataError(
            "INVALID_ARGUMENT", "Параметры не соответствуют JSON Schema", field=path[:120] or None
        )
    args = request["arguments"]
    tool = request["tool"]
    today = now.date()
    if tool == "search_hotels":
        start, end = date.fromisoformat(args["check_in"]), date.fromisoformat(args["check_out"])
        if end <= start:
            raise DataError("INVALID_ARGUMENT", "Выезд должен быть позже заезда", field="check_out")
        if start < today:
            raise DataError("INVALID_ARGUMENT", "Дата заезда в прошлом", field="check_in")
    elif tool == "search_transport":
        if date.fromisoformat(args["departure_date"]) < today:
            raise DataError("INVALID_ARGUMENT", "Дата отправления в прошлом", field="departure_date")
    elif tool == "get_weather":
        start, end = date.fromisoformat(args["start_date"]), date.fromisoformat(args["end_date"])
        if end <= start:
            raise DataError("INVALID_ARGUMENT", "Конец периода должен быть позже начала", field="end_date")
        if (end - start).days > 16:
            raise DataError(
                "UNSUPPORTED_REQUEST", "Версия 1.0.0 поддерживает не более 16 дней", field="end_date"
            )
        if start < today:
            raise DataError("INVALID_ARGUMENT", "Начало прогноза в прошлом", field="start_date")
    elif args["departure_at"] is not None:
        departure = datetime.fromisoformat(args["departure_at"].replace("Z", "+00:00"))
        if departure < now.astimezone(UTC):
            raise DataError("INVALID_ARGUMENT", "Время отправления в прошлом", field="departure_at")


def assert_response(request, response):
    """Schema AND cross-field invariants; callable by the consuming backend too."""

    def require(condition):
        if not condition:
            raise ValidationError("Ответ нарушает межполевые проверки контракта")

    ensure_finite(response)
    RESPONSE_VALIDATORS[request["tool"]].validate(response)
    items = response["data"]["items"]
    args, tool = request["arguments"], request["tool"]
    require(response["request_id"] == request["request_id"])
    require(response["meta"]["returned_count"] == len(items))
    if response["status"] == "error":
        require(not items)
        return
    require(response["error"] is None)
    if tool in ("search_hotels", "search_transport"):
        require(len(items) <= args["limit"])
        require(len({item["id"] for item in items}) == len(items))
    for item in items:
        if tool == "search_hotels":
            require(item["check_in"] == args["check_in"] and item["check_out"] == args["check_out"])
            require(item["guests"] == args["guests"])
            required = {"requested_guests_price", "mandatory_fees"}
            required |= {key for key in ("max_total_price_minor", "meal") if args[key] is not None}
            required |= {key for key in ("pets_required", "free_cancellation_required") if args[key]}
            checks = {c["field"]: c["status"] for c in item["checks"]}
            require(required == set(checks) and len(checks) == len(item["checks"]))
            require("failed" not in checks.values())
            if "unknown" in checks.values():
                require(item["match_status"] == "requires_confirmation")
            if args["meal"] is not None and item["meal"] is not None:
                require(item["meal"] == args["meal"])
            if args["pets_required"]:
                require(item["pets_allowed"] is not False)
        elif tool == "get_weather":
            require(args["start_date"] <= item["date"] < args["end_date"])
            for prefix in ("day", "night"):
                lo, hi = item[f"{prefix}_temp_min_c"], item[f"{prefix}_temp_max_c"]
                require(lo is None or hi is None or lo <= hi)
        elif tool == "search_transport":
            require(item["transport_type"] == args["transport_type"])
            require(not args["direct_only"] or len(item["segments"]) == 1)
            previous = None
            for segment in item["segments"]:
                departure = segment["departure_at"]
                arrival = segment["arrival_at"]
                if departure and arrival:
                    dep, arr = datetime.fromisoformat(departure), datetime.fromisoformat(arrival)
                    require(dep <= arr and (previous is None or previous <= dep))
                    previous = arr
            first = item["segments"][0]["departure_at"]
            require(first is None or first[:10] == args["departure_date"])
            if item["total_price"] is None:
                require(item["match_status"] == "requires_confirmation")
        else:
            require(item["origin"] == args["origin"] and item["destination"] == args["destination"])
            require(item["departure_at"] == args["departure_at"])
    if tool == "get_weather":
        require(len({item["date"] for item in items}) == len(items))
