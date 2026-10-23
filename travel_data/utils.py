import hashlib
import json
import math
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def stable_id(prefix, *parts):
    return f"{prefix}_{hashlib.sha256(canonical(parts).encode()).hexdigest()[:24]}"


def utc_iso(value=None):
    return (value or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")


def text(value, size=500):
    return value[:size] if isinstance(value, str) else None


def safe_url(value):
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return None
    value = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return value if len(value) <= 1500 else None


def number(value, *, minimum=None, maximum=None, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Expected a finite number")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError("Number is out of range")
    if integer and value != int(value):
        raise ValueError("Expected an integer")
    return int(value) if integer else value


def minor_units(value):
    try:
        amount = Decimal(str(value)) * 100
        if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
            return None
        return int(amount)
    except (InvalidOperation, ValueError):
        return None


def timestamp(value, *, documented_utc=False):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            if not documented_utc:
                return None
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.isoformat()
    except ValueError:
        return None


def ensure_finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("NaN and Infinity are forbidden")
    if isinstance(value, dict):
        for item in value.values():
            ensure_finite(item)
    elif isinstance(value, list):
        for item in value:
            ensure_finite(item)
