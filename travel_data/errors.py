from dataclasses import dataclass

ERROR_CODES = [
    "INVALID_ARGUMENT",
    "AMBIGUOUS_LOCATION",
    "UNSUPPORTED_REQUEST",
    "ACCESS_NOT_CONFIGURED",
    "SOURCE_AUTH_FAILED",
    "SOURCE_RATE_LIMITED",
    "SOURCE_TIMEOUT",
    "SOURCE_UNAVAILABLE",
    "SOURCE_SCHEMA_CHANGED",
    "OUT_OF_COVERAGE",
]


@dataclass
class DataError(Exception):
    code: str
    message: str
    retryable: bool = False
    field: str | None = None

    def as_dict(self):
        return {"code": self.code, "message": self.message, "retryable": self.retryable, "field": self.field}
