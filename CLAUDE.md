# CLAUDE.md

> 🇺🇦 Українська - основна. English summary нижче.

## Про проєкт
Telegram Archiver - локальний архіватор власного Telegram-акаунта. Специфікація в `docs/`. Перед роботою прочитай:
`docs/00-overview.md`, `docs/02-architecture.md`, `docs/19-private-chats.md`, `docs/11-data-model.md`, `docs/17-ux-performance.md`, `docs/14-roadmap.md`.

## Стек
- Backend: Python 3.12, uv, Telethon, FastAPI, uvicorn, pydantic v2, aiosqlite, keyring.
  Extras: `[whisper]` faster-whisper; `[miniapps]` playwright.
- Frontend: Vue 3 + Vite + TypeScript + Pinia + Naive UI, vue-i18n (uk - дефолт, en).
- Збірка: Nuitka (локально), Tauri 2 - опційно на M9.

## Структура
```
backend/tgarchiver/{core,tg,sync,media,export,miniapps,transcribe,monitor,ai,jobs,api}
frontend/src/{views,components,stores,api,i18n}
scripts/*.ps1
docs/
```

## Команди
| Дія | Команда |
|---|---|
| Запуск backend | `cd backend; uv run tgarchiver` |
| Тести backend | `uv run pytest -q` |
| Лінт | `uv run ruff check . ; uv run mypy tgarchiver` |
| Frontend dev | `cd frontend; pnpm dev` |
| Frontend тести | `pnpm vitest run ; pnpm vue-tsc --noEmit` |
| Збірка | `./scripts/build.ps1` |

## Правила (обов'язкові)
1. Працювати по мілстоунах `docs/14-roadmap.md`, по одному. Кінець мілстоуну = тести зелені + коротке демо в описі.
2. Усі довгі операції - через jobs-движок: pause/resume/cancel/retry, прогрес через WS, checkpoint у БД.
3. FloodWait ніколи не ігнорувати і не ретраїти раніше. `flood_sleep_threshold=0`, обробляти вручну.
4. Експорт - тільки потоковий і частинами (docs/16). Не тримати чат у пам'яті.
5. Ніколи не логувати: тексти повідомлень, session string, `tgWebAppData`, API-ключі.
6. Mini Apps auto crawl ніколи не натискає платіжні/підтверджувальні елементи.
7. UX: дефолти прості, налаштування - під "Додатково". Бюджети продуктивності з docs/17 - це acceptance criteria.
8. Весь UI-текст через i18n, спочатку українською, потім англійською.
9. Нові залежності - мінімально, пояснювати навіщо. Важке - лише в extras з ледачим імпортом.
10. Назви TL-методів звіряти з поточною версією Telethon (`telethon.tl.functions`), не вигадувати.
11. Секретні чати не підтримуються API. Захищений контент (noforwards) підтримується повністю через API за перемикачем (docs/19).
12. Windows first: шляхи через pathlib, безпечні імена файлів, PowerShell-скрипти.

## Перша задача
M0: монорепо, pyproject з extras, FastAPI + токен, SQLite міграції, jobs-движок з тестовою job, WS, Vue-оболонка з Dashboard і i18n.

---

## English summary
Telegram Archiver is a local archiver for the user's own Telegram account. Read `docs/00, 02, 11, 17, 14` first.
Work milestone by milestone. All long operations go through the jobs engine (pause/resume/retry, WS progress, DB checkpoints).
Never ignore FloodWait. Stream exports in chunks. Never log message texts, sessions, `tgWebAppData` or API keys.
Mini App crawler must never click payment/confirm elements. UI strings via i18n (uk default, en). Main README is English (README.md), Ukrainian in README.uk.md. Protected (noforwards) content is fully supported behind an opt-in toggle.
Heavy deps (faster-whisper, playwright) only as lazy-loaded extras. Verify Telethon TL method names; secret chats are not available via API.
