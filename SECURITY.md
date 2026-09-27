# Security Policy

Telegram Archiver handles a Telegram session, which is full access to an account. Please report vulnerabilities privately.

## Reporting

- Open a private GitHub security advisory (Security → Report a vulnerability), or email the maintainers.
- Do not open public issues for vulnerabilities and do not attach real `.session` files, session strings, `tgWebAppData`, API keys or archives with other people's messages.

## Design notes

- The server listens on `127.0.0.1` only, requires a random per-run token and rejects non-loopback `Host` headers (DNS rebinding).
- The session string, `api_hash` and AI keys are stored in the OS secure storage (Windows Credential Manager / DPAPI, macOS Keychain, Secret Service), never in plain files.
- Logs are passed through a redaction filter; message texts, sessions, `tgWebAppData` and API keys are never logged.
- No telemetry. Network access: Telegram, the AI provider you choose, GitHub releases (update check, can be disabled), Whisper model downloads.
