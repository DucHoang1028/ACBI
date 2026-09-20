param([ValidateSet('up','down','test','eval','seed','seed-users','verify-users','verify-chat','verify-rag','verify-phase4')][string]$Action = 'up')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$composeArgs = @('compose','--env-file','deploy/.env','-f','deploy/docker-compose.yml','-f','deploy/docker-compose.local.yml')
switch ($Action) {
  'up' { & docker @composeArgs up -d --build --wait }
  'down' { & docker @composeArgs down }
  'seed' { & '.tools/acbi-env/Scripts/python.exe' scripts/prepare_warehouse.py }
  'seed-users' {
    & docker @composeArgs exec -T backend python scripts/seed_users.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & docker cp acbi-backend-1:/tmp/acbi-seed-credentials.txt deploy/seed-credentials.txt
    if ($LASTEXITCODE -ne 0) { Write-Error 'Accounts already exist; private credential file was not replaced.'; exit 1 }
    & docker @composeArgs exec -T backend python -c "from pathlib import Path; Path('/tmp/acbi-seed-credentials.txt').unlink(missing_ok=True)"
  }
  'verify-users' {
    if (-not (Test-Path -LiteralPath 'deploy/seed-credentials.txt')) { throw 'Seed credentials file is missing.' }
    Get-Content -LiteralPath 'deploy/seed-credentials.txt' -Raw | & docker @composeArgs exec -T backend python scripts/verify_phase1.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & docker cp acbi-backend-1:/tmp/acbi-phase1/phase1-verification.json docs/phase1-verification.json
  }
  'verify-chat' {
    if (-not (Test-Path -LiteralPath 'deploy/seed-credentials.txt')) { throw 'Seed credentials file is missing.' }
    Get-Content -LiteralPath 'deploy/seed-credentials.txt' -Raw | & docker @composeArgs exec -T backend python scripts/verify_phase2.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & docker cp acbi-backend-1:/tmp/acbi-phase2.json docs/phase2-verification.json
  }
  'verify-rag' {
    if (-not (Test-Path -LiteralPath 'deploy/seed-credentials.txt')) { throw 'Seed credentials file is missing.' }
    Get-Content -LiteralPath 'deploy/seed-credentials.txt' -Raw | & docker @composeArgs exec -T backend python scripts/verify_phase3.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & docker cp acbi-backend-1:/tmp/acbi-phase3.json docs/phase3-verification.json
  }
  'verify-phase4' {
    if (-not (Test-Path -LiteralPath 'deploy/seed-credentials.txt')) { throw 'Seed credentials file is missing.' }
    Get-Content -LiteralPath 'deploy/seed-credentials.txt' -Raw | & docker @composeArgs exec -T backend python scripts/verify_phase4.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & docker cp acbi-backend-1:/tmp/acbi-phase4.json docs/phase4-verification.json
  }
  'test' { & '.tools/acbi-env/Scripts/python.exe' -m pytest -q }
  'eval' {
    & docker @composeArgs exec -T -e ACBI_REPORT_DIR=/tmp/acbi-phase0 backend python scripts/verify_phase0.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & docker cp acbi-backend-1:/tmp/acbi-phase0/docs/phase0-verification.json docs/phase0-verification.json
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & docker cp acbi-backend-1:/tmp/acbi-phase0/data/eval/results.local.json data/eval/results.local.json
  }
}
exit $LASTEXITCODE
