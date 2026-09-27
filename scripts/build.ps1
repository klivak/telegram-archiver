# Full local build: frontend -> backend exe (-> Tauri, when src-tauri exists and cargo is installed).
param(
    [ValidateSet('nuitka', 'pyinstaller')]
    [string]$Tool = 'nuitka'
)
$ErrorActionPreference = 'Stop'
& "$PSScriptRoot\build-frontend.ps1"
& "$PSScriptRoot\build-backend.ps1" -Tool $Tool

$root = Split-Path -Parent $PSScriptRoot
if ((Test-Path (Join-Path $root 'src-tauri')) -and (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Push-Location $root
    try { pnpm --dir frontend tauri build } finally { Pop-Location }
}
