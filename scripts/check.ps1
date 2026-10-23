$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
& .\.venv\Scripts\python.exe -m ruff check .
if ($LASTEXITCODE -ne 0) { throw 'Code checks failed.' }
& .\.venv\Scripts\python.exe scripts/build_contracts.py --check
if ($LASTEXITCODE -ne 0) { throw 'Exported contracts do not match the implementation.' }
& .\.venv\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
