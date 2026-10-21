# Ollama Serve Autostart Manual Test (not CI)

> Status: **PERFORMED 2026-09-19** — all steps passed with real processes
> (tray quit → owned serve → owned stop → tray restored). Re-run after
> launcher changes; check every box again.

Validates the launcher-owned `ollama serve` lifecycle: auto-start when
down, pre-existing servers left alone, owned servers stopped on exit.
Requires quitting the Ollama tray app first — it otherwise restarts the
server itself and the down-path cannot be observed.

Related runbooks: [lan-readiness](lan-readiness.md) (firewall/TLS),
[windows-firewall](windows-firewall.md); ASCS one-command launcher UX is
covered by `ASCS/docs/tui-manual-test.md` in the ASCS repo (separate
remote — linked by path, not URL).

## 1. Preflight

- [x] `ollama ps` shows a running server; note its PID
      (`Get-Process ollama`).
- [x] Record whether an `ollama app` (tray) process exists.

## 2. Pre-existing server is left alone

With the server running:

```powershell
cd C:\Users\<you>\Desktop\RISARMS\ASCS
npm run ascs:check
```

- [x] Check passes; `ollama` PID is unchanged afterwards.
- [x] Tray app (if present) still reports running.

## 3. Owned autostart (tray quit required)

1. Quit the Ollama tray app; `taskkill /IM ollama.exe /F`;
   confirm `ollama ps` fails / connection refused.
2. Run `npm run ascs:check`.

- [x] Launcher prints `Ollama is down; starting 'ollama serve' …`
      then `Ollama server is up (launcher-owned).`
- [x] Check output shows `Ollama: OK` with an available model.
- [x] A new `ollama` server PID exists.

## 4. Owned stop on exit

After step 3, quit the session / let the launcher exit.

- [x] The launcher-owned server PID is gone (`Get-Process ollama`
      empty); model unloaded first (`ollama stop` best-effort).
- [x] Restart the tray app / `ollama serve` to restore the normal
      state afterwards.

## 5. Failure branch

With the server down AND the port blocked (or `ollama` renamed off
PATH in a sandbox copy — never on the real install):

- [x] Launcher fails fast with the `ollama serve` hint, exit code `2`,
      no 60s hang, no stray processes.
