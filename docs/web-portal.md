# C.O.R.E. web portal

The portal is now part of CORE-HOST, at `python -m core web`. It adds profiles,
authenticated browser sessions, RISARMS service discovery, on-demand ASIS/ASCS
tasks, streamed process output, Stop controls, owner administration and
profile-scoped RESCS records. The existing TCP device protocol is retained.

## Start locally

1. Install Python 3.10+ for C.O.R.E. ASCS itself requires Python 3.12+.
2. Extract CORE-HOST and CORE-CLIENT. Put your existing ASIS, ASCS, RESCS,
   TIVISS and ROVERT source directories beside CORE-HOST, or set their paths
   later in Settings. Keep each service's installed `.venv` in its own repo.
3. Windows: double-click `run-core.cmd`. macOS: run `sh run-core.command`.
   These create a CORE-only virtual environment on first launch and install
   its package dependencies. Internet is needed for first-time pip installs.
4. The host opens `http://127.0.0.1:8780`. Copy the setup key printed in the
   host window, create your owner username/password, and sign in.
5. In Settings, confirm repository paths. The portal uses each service's
   `.venv` Python when present; otherwise it uses the host interpreter.
   Dependencies for external services must be installed in their own repos.
6. Start RESCS from Settings, or configure an existing RESCS HTTPS endpoint
   and API key. The managed local RESCS instance listens on 127.0.0.1:8081.
7. Use the assistant/coding workspace to run ASIS or ASCS. `/asis ...` and
   `/ascs ...` route a prompt explicitly. A service is not reported as ready
   merely because its source exists; launch errors appear in the output.

Doctor: `python scripts/launch_web.py doctor`. Existing environments can
install/update the host with `.venv\Scripts\python.exe -m pip install -e .`.

## Profiles and storage

The first profile is the owner. Only the owner can create member profiles,
configure repositories, run code tasks, or start the backend server. Members
can run ASIS general mode and access their own workspace records.

Authentication is local to CORE: salted scrypt password hashes, hashed session
tokens, 12-hour sessions, HttpOnly/SameSite cookies, CSRF validation and a
bounded login-attempt window. The account database is not an application
document store. Shared user records are sent to the real RESCS v1 API.

For profile UUID `id`, CORE derives `owner=core.profile.id` and
`namespace=personal.profile.id.service`. Browser-supplied owner fields are
ignored. Responses with a foreign owner or namespace fail closed. ASIS local
memory/config/cache/log directories and ASCS workspaces/experience files are
separate under `var/web/profiles/id/`. These are application boundaries, not
OS containers. ASCS remains owner-only because it can execute code with the
host account's privileges. Save finished service results to RESCS using the
Save result control. A missing RESCS backend is an explicit error; CORE does
not pretend a local fallback was persisted remotely.

## Internet hosting

The gateway binds only to localhost. Publish **8780**, behind a same-machine
HTTPS reverse proxy or authenticated private-network gateway. Never publish
the old unauthenticated 8765 host portal or a raw ASCS/Ollama/RESCS listener.

Example with your own domain and Caddy:

1. Set `public_origin` in Settings to `https://your-domain.example`.
2. Copy `deploy/Caddyfile.example` to a Caddyfile and replace the example
   domain. Point DNS to the server and configure the required HTTPS ingress.
3. Run Caddy with that file on the Windows host. Let Caddy obtain a real TLS
   certificate. CORE validates Host and Origin against the configured domain;
   public-origin cookies carry Secure. Browser clients open that HTTPS URL.

`CORE_PUBLIC_ORIGIN` can also set the HTTPS origin before launch. The portal
does not automatically purchase domains, create DNS records or deploy a
tunnel. No public deployment has been performed by the build environment.

## Current service adapters

| Service | Integration |
|---|---|
| ASIS | Real CLI, general mode, per-profile memory and directories; on-demand task output |
| ASCS | Real `agent.main` CLI, plan/build/auto, owner only, per-profile workspace |
| RESCS | Actual v1 records API; optional managed local uvicorn process |
| TIVISS | Real one-shot CLI with Ollama and per-profile SQLite memory; owner only |
| ROVERT | Discovered service card and owner-configured telemetry link; no automatic hardware commands |

ASIS and ASCS require their actual local Ollama/model setup. CORE does not
replace either agent with an OpenAI call or a mock. TIVISS requires an installed model name in Settings; its default mock provider is explicitly replaced with Ollama. ROVERT still needs an owner-configured telemetry link; no hardware commands are invented or sent automatically.

## Persistence and shutdown

Keep `var/web/` and the service repositories between upgrades. Account/config
files contain credentials and must not be shared. Back up this directory and
the RESCS database/object-store together. Local POSIX credential files use
mode 0600; set appropriate Windows ACLs for the host account.

Leaving the host window open keeps the portal available. Ctrl+C stops only
the service processes CORE launched; it does not claim ownership of unrelated
existing services. Tasks have a 15-minute execution limit and bounded output
history. Closing the browser does not cancel a task; use Stop explicitly.

The DayQuest reference informed the authoritative visible Windows server,
encrypted client access and profile separation. LibraryHQ/Our Library informed
first-run setup, account roles and operational logging. Its UI was not copied.
The source ZIP references could not be materialized in this session because
the transfer helper's DNS was unavailable; DayQuest's architecture document
and the current RISARMS GitHub contracts were inspected directly.
