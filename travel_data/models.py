from dataclasses import dataclass, field


@dataclass
class Candidate:
    item: dict
    facts: dict = field(default_factory=dict)


@dataclass
class ProviderResult:
    candidates: list[Candidate] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    has_more: bool = False
    source_updated_at: str | None = None
    incomplete: bool = False


def warning(code, message):
    return {"code": code, "message": message[:500]}
