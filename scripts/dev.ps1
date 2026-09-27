# Dev mode: backend (--dev, port 8765) in a new window + Vite dev server with hot reload here.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$root\backend'; uv run tgarchiver --dev --no-browser"
Push-Location (Join-Path $root 'frontend')
try {
    if (-not (Test-Path node_modules)) { pnpm install }
    Start-Sleep -Seconds 2
    Start-Process 'http://localhost:5173/'
    pnpm dev
} finally {
    Pop-Location
}
