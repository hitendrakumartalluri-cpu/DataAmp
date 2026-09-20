Set-Location "$PSScriptRoot\..\..\services\control-plane"
$env:PYTHONPATH="."
if (-not $env:AMP_DEMO_MODE) { $env:AMP_DEMO_MODE="true" }
if (-not $env:AMP_DATABASE_URL) { $env:AMP_DATABASE_URL="sqlite:///$(Get-Location)\amp.db" }
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
