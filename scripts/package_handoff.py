"""Package only explicit project paths; never credentials, environments or imported catalogues."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ("travel_data", "schemas", "examples", "tests", "scripts", "docs", ".github", ".vscode")
FILES = (
    "README.md",
    "README.txt",
    "pyproject.toml",
    "MANIFEST.in",
    "requirements-lock.txt",
    "tools.json",
    ".env.example",
    ".gitignore",
    "START.cmd",
    "INSTALL.cmd",
    "OPEN_CODE.cmd",
)


def main():
    destination = ROOT / "dist" / "travel-data-handoff-1.0.0.zip"
    destination.parent.mkdir(exist_ok=True)
    members = [ROOT / name for name in FILES]
    for directory in DIRECTORIES:
        path = ROOT / directory
        if path.exists():
            members.extend(
                file
                for file in path.rglob("*")
                if file.is_file() and "__pycache__" not in file.parts and file.suffix not in (".pyc", ".pyo")
            )
    for name in ("verification.json", "live-check.json"):
        path = ROOT / "reports" / name
        if path.is_file():
            members.append(path)
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for file in sorted(set(members)):
            if not file.is_file():
                raise FileNotFoundError(file)
            archive.write(file, file.relative_to(ROOT).as_posix())
    print(destination)


if __name__ == "__main__":
    main()
