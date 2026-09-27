# One-click start: finds or installs uv, builds the UI on first run, starts the app and opens the browser.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$uvDirs = @("$env:APPDATA\Python\Python311\Scripts", "$env:APPDATA\Python\Python312\Scripts", "$env:USERPROFILE\.local\bin", "$env:USERPROFILE\.cargo\bin")
foreach ($d in $uvDirs) { if (Test-Path (Join-Path $d 'uv.exe')) { $env:Path = "$d;$env:Path" } }
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'Installing uv (one time)...' -ForegroundColor Cyan
    powershell -ExecutionPolicy Bypass -c 'irm https://astral.sh/uv/install.ps1 | iex'
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

$dist = Join-Path $root 'frontend\dist\index.html'
if (-not (Test-Path $dist)) {
    if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) { throw 'UI is not built and pnpm is not installed. Install Node.js + pnpm, or use the released exe.' }
    Write-Host 'Building the UI (one time)...' -ForegroundColor Cyan
    Push-Location (Join-Path $root 'frontend')
    try { pnpm install --frozen-lockfile; pnpm build } finally { Pop-Location }
}

Set-Location (Join-Path $root 'backend')
uv run tgarchiver
