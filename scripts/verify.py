"""Run acceptance checks and record live verification separately from offline tests."""

import asyncio
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(*args):
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def main():
    from travel_data.cli import live_check, write_json
    from travel_data.config import Settings
    from travel_data.utils import utc_iso

    run("-m", "ruff", "check", ".")
    run("scripts/build_contracts.py", "--check")
    run("-m", "pytest", "-q", "--junitxml=reports/tests.xml")
    xml = ET.parse(ROOT / "reports" / "tests.xml").getroot()
    suites = [xml] if xml.tag == "testsuite" else list(xml.iter("testsuite"))
    totals = {
        key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "failures", "errors", "skipped")
    }
    live = asyncio.run(live_check(Settings.from_env(ROOT / ".env"), ROOT / "reports" / "live-check.json"))
    report = {
        "checked_at": utc_iso(),
        "python": sys.version.split()[0],
        "offline_checks": {"ruff": "passed", "contract_exports": "passed", "pytest": totals},
        "live_integrations_verified": live["all_verified"],
        "source_results": [
            {
                "source_name": item["response"]["meta"]["source_name"],
                "verified": item["verified"],
                "status": item["response"]["status"],
                "error_code": (item["response"]["error"] or {}).get("code"),
            }
            for item in live["results"]
        ],
        "original_schema_archive_received": False,
        "contract_origin": "reconstructed_from_supplied_docx",
        "acceptance_complete": False,
    }
    write_json(ROOT / "reports" / "verification.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
