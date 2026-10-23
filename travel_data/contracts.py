"""Draft 2020-12 contracts reconstructed from the supplied DOCX, pending original archive review."""

from .errors import ERROR_CODES

VERSION = "1.0.0"
TOOLS = ("search_hotels", "search_transport", "get_weather", "get_route")


def obj(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def string(size=500, *, nullable=False, **kwargs):
    return {"type": ["string", "null"] if nullable else "string", "maxLength": size, **kwargs}


def enum(*values):
    return {"enum": list(values)}


def nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


def array(items, maximum, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


BOOL = {"type": "boolean"}
NBOOL = {"type": ["boolean", "null"]}
MONEY = {"type": "integer", "minimum": 0}
NUMBER = {"type": "number"}
DATE = string(10, format="date", pattern=r"^\d{4}-\d{2}-\d{2}$")
DATETIME = string(40, format="date-time", pattern=r"(Z|[+-]\d{2}:\d{2})$")
COORDS = obj(
    {
        "lat": {"type": "number", "minimum": -90, "maximum": 90},
        "lon": {"type": "number", "minimum": -180, "maximum": 180},
    }
)
CITY = obj({"city": string(120, minLength=1), "country_code": string(2, pattern="^[A-Z]{2}$")})
GUESTS = obj(
    {
        "adults": {"type": "integer", "minimum": 1, "maximum": 9},
        "children_ages": array({"type": "integer", "minimum": 0, "maximum": 17}, 6),
    }
)
LIMIT = {"type": "integer", "minimum": 1, "maximum": 5}
MEAL = enum("RO", "BB", "HB", "FB", "AI", None)
URL = string(1500, nullable=True, format="uri", pattern=r"^https?://")
CHECK = obj(
    {
        "field": enum(
            "max_total_price_minor",
            "meal",
            "pets_required",
            "free_cancellation_required",
            "requested_guests_price",
            "mandatory_fees",
        ),
        "status": enum("passed", "failed", "unknown"),
    }
)
MATCH = enum("matches", "requires_confirmation")
PRICE = obj(
    {
        "amount_minor": MONEY,
        "currency": enum("RUB"),
        "basis": enum("requested_guests_total"),
        "includes_mandatory_fees": NBOOL,
    }
)

ARGUMENTS = {
    "search_hotels": obj(
        {
            "location": CITY,
            "check_in": DATE,
            "check_out": DATE,
            "guests": GUESTS,
            "max_total_price_minor": nullable(MONEY),
            "meal": MEAL,
            "pets_required": BOOL,
            "free_cancellation_required": BOOL,
            "limit": LIMIT,
        }
    ),
    "search_transport": obj(
        {
            "origin": CITY,
            "destination": CITY,
            "departure_date": DATE,
            "transport_type": enum("plane", "train", "bus"),
            "guests": GUESTS,
            "direct_only": BOOL,
            "limit": LIMIT,
        }
    ),
    "get_weather": obj({"location": COORDS, "start_date": DATE, "end_date": DATE}),
    "get_route": obj(
        {"origin": COORDS, "destination": COORDS, "departure_at": nullable(DATETIME), "mode": enum("car")}
    ),
}

HOTEL = obj(
    {
        "id": string(160, minLength=1),
        "hotel_name": string(300, minLength=1),
        "offer_id": string(300, nullable=True),
        "room_name": string(300, nullable=True),
        "address": string(500, nullable=True),
        "coordinates": nullable(COORDS),
        "check_in": DATE,
        "check_out": DATE,
        "guests": GUESTS,
        "meal": MEAL,
        "total_price": nullable(PRICE),
        "cancellation": nullable(obj({"free_until": nullable(DATETIME), "summary": string(500)})),
        "pets_allowed": NBOOL,
        "match_status": MATCH,
        "checks": array(CHECK, 6),
        "source_url": URL,
    }
)
PLACE = obj({"name": string(300), "code": string(80, nullable=True)})
SEGMENT = obj(
    {
        "origin": PLACE,
        "destination": PLACE,
        "departure_at": nullable(DATETIME),
        "arrival_at": nullable(DATETIME),
        "number": string(80, nullable=True),
    }
)
TRANSPORT = obj(
    {
        "id": string(160, minLength=1),
        "transport_type": enum("plane", "train", "bus"),
        "carrier": string(300, nullable=True),
        "segments": array(SEGMENT, 12, 1),
        "duration_s": nullable({"type": "integer", "minimum": 0}),
        "total_price": nullable(PRICE),
        "advertised_price": nullable(
            obj({"amount_minor": MONEY, "currency": enum("RUB"), "basis": enum("one_adult", "unknown")})
        ),
        "price_kind": enum("live_quote", "cached_quote", "schedule_only", "unknown"),
        "baggage": string(500, nullable=True),
        "match_status": MATCH,
        "checks": array(CHECK, 6),
        "source_url": URL,
    }
)
WEATHER = obj(
    {
        "date": DATE,
        "kind": enum("forecast"),
        "day_temp_min_c": nullable(NUMBER),
        "day_temp_max_c": nullable(NUMBER),
        "night_temp_min_c": nullable(NUMBER),
        "night_temp_max_c": nullable(NUMBER),
        "water_temp_c": nullable(NUMBER),
        "precipitation_probability": nullable({"type": "number", "minimum": 0, "maximum": 1}),
        "source_url": URL,
    }
)
ROUTE = obj(
    {
        "id": string(160, minLength=1),
        "origin": COORDS,
        "destination": COORDS,
        "distance_m": {"type": "integer", "minimum": 0},
        "duration_s": {"type": "integer", "minimum": 0},
        "traffic_mode": enum("live", "historical", "none", "unknown"),
        "departure_at": nullable(DATETIME),
        "source_url": URL,
    }
)
ITEMS = dict(zip(TOOLS, (HOTEL, TRANSPORT, WEATHER, ROUTE), strict=True))
MAX_ITEMS = dict(zip(TOOLS, (5, 5, 16, 1), strict=True))
WARNING = obj({"code": string(80, minLength=1), "message": string(500, minLength=1)})
ERROR = obj(
    {
        "code": enum(*ERROR_CODES),
        "message": string(500),
        "retryable": BOOL,
        "field": string(120, nullable=True),
    }
)
META = obj(
    {
        "source_name": string(120),
        "fetched_at": DATETIME,
        "source_updated_at": nullable(DATETIME),
        "cache_hit": BOOL,
        "is_stale": BOOL,
        "is_fixture": BOOL,
        "returned_count": {"type": "integer", "minimum": 0, "maximum": 16},
        "has_more": BOOL,
    }
)


def document(schema, filename):
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:ostrovok-hackathon:travel-data:{VERSION}:{filename}",
        **schema,
    }


REQUEST_SCHEMAS = {
    tool: document(
        obj(
            {
                "schema_version": {"const": VERSION},
                "request_id": string(120, minLength=1),
                "tool": {"const": tool},
                "arguments": ARGUMENTS[tool],
            }
        ),
        f"{tool}.request",
    )
    for tool in TOOLS
}
RESPONSE_SCHEMAS = {}
for _tool in TOOLS:
    _schema = obj(
        {
            "schema_version": {"const": VERSION},
            "request_id": string(120, minLength=1),
            "tool": {"const": _tool},
            "status": enum("ok", "empty", "partial", "error"),
            "data": obj({"items": array(ITEMS[_tool], MAX_ITEMS[_tool])}),
            "meta": META,
            "warnings": array(WARNING, 24),
            "error": nullable(ERROR),
        }
    )
    _schema["allOf"] = [
        {
            "if": {"properties": {"status": {"const": "error"}}},
            "then": {
                "properties": {
                    "error": ERROR,
                    "data": obj({"items": array(ITEMS[_tool], 0)}),
                    "meta": {"properties": {"returned_count": {"const": 0}}},
                }
            },
            "else": {"properties": {"error": {"type": "null"}}},
        },
        {
            "if": {"properties": {"status": {"const": "empty"}}},
            "then": {"properties": {"data": obj({"items": array(ITEMS[_tool], 0)})}},
        },
        {
            "if": {"properties": {"status": {"const": "ok"}}},
            "then": {"properties": {"data": obj({"items": array(ITEMS[_tool], MAX_ITEMS[_tool], 1)})}},
        },
        {
            "if": {"properties": {"status": {"const": "partial"}}},
            "then": {"properties": {"warnings": array(WARNING, 24, 1)}},
        },
    ]
    RESPONSE_SCHEMAS[_tool] = document(_schema, f"{_tool}.response")
