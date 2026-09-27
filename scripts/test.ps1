# Runs every check the milestone definition of done requires (CLAUDE.md rule 1).
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

Push-Location (Join-Path $root 'backend')
try {
    uv run ruff check .; if ($LASTEXITCODE) { throw 'ruff' }
    uv run mypy tgarchiver; if ($LASTEXITCODE) { throw 'mypy' }
    uv run pytest -q; if ($LASTEXITCODE) { throw 'pytest' }
} finally { Pop-Location }

Push-Location (Join-Path $root 'frontend')
try {
    pnpm vitest run; if ($LASTEXITCODE) { throw 'vitest' }
    pnpm vue-tsc --noEmit; if ($LASTEXITCODE) { throw 'vue-tsc' }
} finally { Pop-Location }
Write-Host 'All checks passed' -ForegroundColor Green
