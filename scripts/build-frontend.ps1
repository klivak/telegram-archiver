# Builds the Vue UI and copies it into the backend package (served by FastAPI in portable mode).
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

Push-Location (Join-Path $root 'frontend')
try {
    pnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw 'pnpm install failed' }
    pnpm build
    if ($LASTEXITCODE -ne 0) { throw 'frontend build failed' }
} finally {
    Pop-Location
}

$web = Join-Path $root 'backend\tgarchiver\web'
if (Test-Path $web) { Remove-Item -Recurse -Force $web }
Copy-Item -Recurse (Join-Path $root 'frontend\dist') $web
Write-Host "Frontend built -> $web" -ForegroundColor Green
