import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    freeze = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "freeze",
            "--all",
            "--exclude",
            "pip",
            "--exclude",
            "ostrovok-travel-data",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    text = "# Development and runtime dependencies verified on Windows / Python 3.15.1.\n" + freeze.stdout
    (ROOT / "requirements-lock.txt").write_text(text, encoding="utf-8")
    print("Wrote requirements-lock.txt")


if __name__ == "__main__":
    main()
