# Contributing

Thanks for helping! Please read [`CLAUDE.md`](CLAUDE.md) (rules) and the spec in [`docs/`](docs/) first.

## Setup

```powershell
# Python 3.12 + uv, Node 20+ + pnpm
cd backend; uv sync            # add --extra whisper / --extra miniapps for optional modules
cd ../frontend; pnpm install
./scripts/dev.ps1              # backend --dev + Vite with hot reload
```

## Checks (must be green)

```powershell
./scripts/test.ps1             # ruff, mypy, pytest, vitest, vue-tsc
```

## Rules in short

- Long operations go through the jobs engine (pause/resume/cancel/retry, WS progress, DB checkpoints).
- Never ignore `FloodWait` or retry before it elapses; `flood_sleep_threshold=0`.
- Exports are streamed in chunks; never hold a whole chat in memory.
- Never log message texts, sessions, `tgWebAppData` or API keys.
- All UI text via i18n: Ukrainian first, then English (`tests/i18n.spec.ts` checks keys).
- Verify Telethon TL names against the installed version (`telethon.tl.functions`).
- New dependencies: minimal and justified; heavy ones only as lazy-loaded extras.
- Never commit `.session` files, configs with keys or archives.
