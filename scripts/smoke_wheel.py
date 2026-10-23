"""Import the built pure-Python wheel, rather than the source checkout, and run all four tools."""

import json
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    wheel = next((root / "dist").glob("ostrovok_travel_data-1.0.0-*.whl"))
    sys.path.insert(0, str(wheel))
    import travel_data
    from travel_data import Settings, call
    from travel_data.client import make_request
    from travel_data.contracts import TOOLS
    from travel_data.demo import ARGUMENTS

    if str(wheel) not in travel_data.__file__:
        raise RuntimeError("Smoke check imported the checkout instead of the wheel")
    results = []
    for tool in TOOLS:
        response = call(make_request(tool, ARGUMENTS[tool]), settings=Settings(), mode="fixture")
        if response["status"] != "ok" or not response["meta"]["is_fixture"]:
            raise RuntimeError(f"Wheel smoke failed: {tool}")
        results.append({"tool": tool, "status": response["status"], "is_fixture": True})
    report_path = root / "reports" / "verification.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["wheel_smoke"] = {"passed": True, "tools": results}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Built wheel: all four fixture tools passed")


if __name__ == "__main__":
    main()
