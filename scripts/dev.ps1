# Dev mode: backend (--dev, port 8765) in a new window + Vite dev server with hot reload here.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$root\backend'; uv run tgarchiver --dev --no-browser"
Push-Location (Join-Path $root 'frontend')
try {
    if (-not (Test-Path node_modules)) { pnpm install }
    # Open the browser only once the backend is up: it writes the dev token that Vite injects into the page.
    Start-Job -ScriptBlock {
        for ($i = 0; $i -lt 120; $i++) {
            try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:8765/api/health' | Out-Null; break } catch { Start-Sleep -Milliseconds 500 }
        }
        Start-Sleep -Seconds 1
        Start-Process 'http://localhost:5173/'
    } | Out-Null
    pnpm dev
} finally {
    Pop-Location
}
