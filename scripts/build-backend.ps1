# Compiles the backend (with the built UI inside) into a single exe.
#   ./scripts/build-backend.ps1                 # Nuitka (release; needs MSVC Build Tools, slow)
#   ./scripts/build-backend.ps1 -Tool pyinstaller  # quick dev build
param(
    [ValidateSet('nuitka', 'pyinstaller')]
    [string]$Tool = 'nuitka'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$out = Join-Path $root 'dist'
New-Item -ItemType Directory -Force $out | Out-Null

if (-not (Test-Path (Join-Path $backend 'tgarchiver\web\index.html'))) {
    throw 'UI is not built. Run scripts/build-frontend.ps1 first.'
}

Push-Location $backend
try {
    uv sync --no-dev
    if ($Tool -eq 'nuitka') {
        $icon = Join-Path $root 'docs\assets\icon.ico'
        $iconArg = if (Test-Path $icon) { "--windows-icon-from-ico=$icon" } else { $null }
        $nuitkaArgs = @(
            '--standalone', '--onefile', '--assume-yes-for-downloads',
            '--output-filename=TelegramArchiver.exe', "--output-dir=$out",
            '--include-package=tgarchiver', '--include-package=uvicorn', '--include-package=websockets',
            '--include-package=keyring.backends', '--include-package=win32ctypes',
            '--include-data-dir=tgarchiver/web=tgarchiver/web',
            # heavy optional extras stay out of the base exe (installed on demand, docs/18)
            '--nofollow-import-to=faster_whisper', '--nofollow-import-to=ctranslate2', '--nofollow-import-to=playwright',
            '--company-name=Zorya Tech Studio', '--product-name=Telegram Archiver', '--file-version=0.1.0', '--product-version=0.1.0',
            '--windows-console-mode=attach'
        )
        if ($iconArg) { $nuitkaArgs += $iconArg }
        uv run --with nuitka --with ordered-set --with zstandard python -m nuitka @nuitkaArgs tgarchiver/__main__.py
    } else {
        uv run --with pyinstaller pyinstaller --noconfirm --onefile --name TelegramArchiver --distpath $out `
            --add-data "tgarchiver/web;tgarchiver/web" --collect-submodules tgarchiver --collect-submodules uvicorn `
            --collect-submodules keyring.backends --exclude-module faster_whisper --exclude-module playwright `
            tgarchiver/__main__.py
    }
    if ($LASTEXITCODE -ne 0) { throw "$Tool build failed" }
} finally {
    Pop-Location
}

$exe = Join-Path $out 'TelegramArchiver.exe'
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
"$hash  TelegramArchiver.exe" | Out-File -Encoding ascii (Join-Path $out 'SHA256SUMS.txt')
Write-Host "Built $exe" -ForegroundColor Green
Write-Host "SHA256 $hash"
