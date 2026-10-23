"""Russian console menu for opening the delivered module by double-clicking START.cmd."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run([sys.executable, *args], cwd=ROOT).returncode


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return run("-m", "travel_data", "demo")
    while True:
        print("\nМодуль данных О!дин запрос\n")
        print("1. Показать демо четырёх функций")
        print("2. Проверить настройку источников")
        print("3. Открыть файл ключей .env")
        print("4. Запустить тесты")
        print("0. Выход")
        try:
            choice = input("\nВыбери номер: ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if choice == "0":
            return 0
        if choice == "1":
            run("-m", "travel_data", "demo")
        elif choice == "2":
            run("-m", "travel_data", "doctor")
        elif choice == "3":
            target = ROOT / ".env"
            if not target.exists():
                shutil.copyfile(ROOT / ".env.example", target)
            subprocess.Popen(["notepad.exe", str(target)])
        elif choice == "4":
            run("-m", "pytest", "-q")
        else:
            print("Введи 1, 2, 3, 4 или 0.")


if __name__ == "__main__":
    raise SystemExit(main())
