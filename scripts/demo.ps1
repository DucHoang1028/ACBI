param([ValidateSet('start','stop','status')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$demoDir = Join-Path (Get-Location) '.tools/demo'
$client = Join-Path (Get-Location) '.tools/cloudflared/cloudflared.exe'
$pidFile = Join-Path $demoDir 'process.txt'
$logFile = Join-Path $demoDir 'tunnel.log'
New-Item -ItemType Directory -Force -Path $demoDir | Out-Null
$tunnel = $null
if (Test-Path $pidFile) {
    $tunnel = Get-Process -Id ([int](Get-Content $pidFile)) -ErrorAction SilentlyContinue
    if ($tunnel -and $tunnel.Path -ne $client) { throw 'Saved process ID belongs to another program; refusing to stop it.' }
}
if ($Action -eq 'stop') {
    if ($tunnel) { Stop-Process -Id $tunnel.Id }
    Write-Output 'Public demo tunnel stopped. Local Docker services remain running.'
    exit
}
if ($Action -eq 'start' -and -not $tunnel) {
    if (-not (Test-Path $client)) { throw 'Install the official cloudflared client at .tools/cloudflared/cloudflared.exe first.' }
    if (-not (Test-Path 'deploy/certs/warehouse-ca.crt')) { throw 'Prepare warehouse TLS first; see docs/LOCAL_DEMO.md.' }
    & docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/docker-compose.local.yml -f deploy/docker-compose.demo.yml up -d --wait
    if ($LASTEXITCODE -ne 0) { throw 'ACBI did not become healthy; tunnel was not started.' }
    # Each restart creates a new public URL. Never keep an old URL as current status.
    if (Test-Path $logFile) { Clear-Content -LiteralPath $logFile }
    $tunnel = Start-Process -FilePath $client -ArgumentList @('tunnel','--url','http://127.0.0.1:8080','--no-autoupdate','--logfile',('"' + $logFile + '"')) -WindowStyle Hidden -PassThru
    $tunnel.Id | Set-Content $pidFile
}
if (-not $tunnel) { Write-Output 'Public demo tunnel is not running.'; exit }
if (Test-Path $logFile) {
    $url = [regex]::Match([string](Get-Content $logFile -Raw), 'https://[a-z0-9-]+\.trycloudflare\.com').Value
    if ($url) { Write-Output $url; exit }
}
Write-Output 'Tunnel is starting. Run ./scripts/demo.ps1 status shortly.'
