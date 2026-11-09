# psd-auto — AI-powered automation for Fedora 45 workstations

`psd-auto` is a workstation autopilot for Fedora Linux 45, written in the
[PSD language](../psd-language/) and compiled to a native binary. It ships a
set of audited, read-mostly system automations, lets you describe *any* new
automation in plain English and have an AI write it for you, and gives you a
local web dashboard to run and monitor everything — with a safety model that
makes “an AI with root-adjacent powers” reasonable.

```console
$ psd-auto doctor            # full read-only health report
$ psd-auto run system-update --dry-run
$ psd-auto add --ai "find which process is using the most memory and print the top 5"
$ psd-auto web               # dashboard on http://127.0.0.1:8099/
```

Everything runs **locally**. The AI is optional and bring-your-own-key; it can
point at OpenAI, OpenRouter, or a fully local Ollama instance — in which case
nothing ever leaves your machine.

---

## Why not just ask ChatGPT for shell commands?

Because outputting `dnf upgrade -y` into a terminal you babysit is exactly how
you get bitten. `psd-auto` is different:

* **Validated specs.** Every automation is a declarative JSON spec that passes
  a strict validator before it is saved — name rules, step shapes, absolute
  timeouts, and a forbidden-pattern list (`mkfs`, `wipefs`, `dd of=/dev/...`,
  `rm -rf /`, pipe-to-shell, `poweroff`, …). AI-generated specs are validated
  the same way human-written ones are.
* **argv steps need no shell.** `["argv", "dnf", "upgrade", "-y"]` cannot be
  shell-injected, period. Raw `shell` steps are allowed for things that truly
  need a pipeline, and are flagged as such.
* **Dry-run before you run.** `--dry-run` prints every command without
  executing it.
* **Destructive things need explicit confirmation** — sudo steps prompt unless
  `--yes` is passed, and `--dry-run` previews them verbatim.
* **Concurrency locking** per automation; stale locks are reported, never
  silently ignored (`--force` overrides deliberately).
* **Everything is logged** to `~/.local/state/psd-auto/logs/` — who/what/when
  and the full output of every step.
* **AI output is never executed blindly** — `add --ai` shows you the generated
  spec and its risk summary before saving, and you can always review the file
  on disk (`~/.config/psd-auto/automations/<name>.json`).

---

## Requirements

| Requirement | Why | Fedora package |
|---|---|---|
| Fedora Linux 45 (target platform) | the built-in automations speak dnf/flatpak/firewalld/SELinux/fwupd/systemd | — |
| python3 ≥ 3.10 | build-time toolchain (PSD compiles through LLVM) | `python3` |
| a C compiler | links the PSD runtime | `gcc` |
| curl | HTTP(S) client at runtime (AI endpoint, `http_get` steps) | `curl` |
| flatpak, fwupd, firewalld, dnf | only used by automations that need them; missing tools degrade to readable warnings, never hard crashes | ship with Workstation |

The PSD toolchain is vendored in this repository under `../psd-language`, so
building offline works — only `pip install llvmlite` (build-time) needs the
network, and the install script skips it if already present.

## Install

```bash
git clone https://github.com/pavit12301611/psd-automation.git
cd psd-automation/fedora-automation
./install.sh
```

`./install.sh`:

1. checks python3 ≥ 3.10, `cc` and `curl`
2. creates/reuses the repo venv and installs `llvmlite`
3. AOT-compiles `src/main.psd` → `build/psd-auto` (atomic rename, so it works
   even while an older `psd-auto` is running)
4. installs a self-contained copy into `~/.local/share/psd-auto` and a
   `~/.local/bin/psd-auto` launcher (override with `PREFIX=/usr/local`)
5. writes a systemd **user** unit for the dashboard (`psd-auto-web.service`)
6. runs `psd-auto init` to create `~/.config/psd-auto` and
   `~/.local/state/psd-auto`

Uninstall: `./install.sh --uninstall` (keeps your specs and logs) ·
`./install.sh --uninstall --purge` (removes everything).

For development, `./psd-auto` in the checkout works without installing — it
uses the repo toolchain directly.

## Quickstart

```bash
psd-auto list                     # 13 built-ins + any of yours
psd-auto doctor                   # read-only system health report
psd-auto info system-update       # what it does + recent runs
psd-auto run system-update --dry-run
psd-auto run system-update
psd-auto logs                     # recent run history
psd-auto web                      # http://127.0.0.1:8099/ (loopback only)
```

### Built-in automations

| Name | Category | sudo | What it does |
|---|---|---|---|
| `system-update` | maintenance | ✓ | dnf metadata refresh, full upgrade, autoremove, clean |
| `security-audit` | security | | read-only posture report: errors today, failed units, firewalld/SELinux, pending security updates, recent logins |
| `disk-cleanup` | cleanup | ✓ | journal vacuum 14d, dnf cache clean, autoremove, flatpak unused runtimes, 30-day thumbnail cache |
| `flatpak-update` | maintenance | | flatpak update + unused runtime removal |
| `backup-home` | backup | | timestamped tar.gz of Documents/Pictures/Music/Videos/Desktop; missing folders skipped |
| `network-check` | network | | ICMP + DNS + HTTPS reachability report (read-only; failures *are* the report) |
| `service-health` | system | | failed units, system state, uptime, memory (read-only) |
| `firmware-check` | maintenance | | pending firmware updates via fwupdmgr — **does not apply them** |
| `tmp-report` | cleanup | | report-only: large stale /tmp files, journal size, autoremove candidates |
| `package-log` | maintenance | | recent package changes, dnf transactions, pending updates (read-only) |
| `doctor` *(native)* | system | | full machine health summary incl. SELinux/firewalld/recent RPMs |
| `largest-dirs` *(native)* | cleanup | | 15 biggest directories directly under $HOME |
| `repo-status` *(native)* | dev | | branch/dirty/ahead summary of every git repo in `~/Projects` (override: `PSD_AUTO_PROJECTS=/path`) |

Native automations are compiled into the binary — they need no shell and are
the fastest path for structured reports.

### The AI features (optional)

Everything works without AI. With a key, three commands light up:

| Command | What it does |
|---|---|
| `psd-auto add --ai "…"` | AI writes a spec for what you described; it is validated, shown to you with a risk summary (`N steps, M raw shell`), and saved as `~/.config/psd-auto/automations/<slug>.json` |
| `psd-auto ask "question"` | Ask about this machine — your prompt is answered using a live, compact machine snapshot (OS, failed units, memory, disk) |
| `psd-auto explain <name>` | AI diagnoses the *last failure* of an automation and suggests a fix |

The web dashboard gets the same three (“Ask AI” page, per-automation “Explain
last failure”, “New automation (AI)” form).

**Configuration** — any OpenAI-compatible chat-completions endpoint:

```bash
# OpenAI
export PSD_AI_API_KEY=sk-...
export PSD_AI_MODEL=gpt-4o-mini            # default
# OpenRouter
export PSD_AI_BASE_URL=https://openrouter.ai/api/v1 PSD_AI_API_KEY=sk-or-... PSD_AI_MODEL=anthropic/claude-3.5-sonnet
# Ollama (local, fully private — no data leaves the machine)
export PSD_AI_BASE_URL=http://localhost:11434/v1 PSD_AI_API_KEY=ollama PSD_AI_MODEL=qwen2.5:7b
```

or put `ai_base_url` / `ai_model` in `~/.config/psd-auto/config.json`.
AI calls time out after 120s and fail closed: if the endpoint is unreachable
the tool prints the error and exits non-zero — it never hangs, and never
partially applies anything.

### The web dashboard

`psd-auto web` serves a server-rendered dashboard (no JavaScript framework, no
external assets — works offline):

* **Dashboard** — machine summary, every automation as a card with sudo/ai/user
  badges, last-run status, one-click *Run* (with dry-run checkbox) and *Remove*
  (user specs only; built-ins are protected server-side)
* **Add automation** — form editor *and* “describe it, AI writes it” mode
* **Logs** — recent runs with full per-step output
* **Ask AI** — chat-style Q&A about your machine

Security model: every route requires a token (`?token=…` or form field). On
loopback the token is optional for convenience; on any routable address a
strong random token is generated automatically and printed at startup — an
unauthenticated command-execution UI is never bound to a network interface.
All dynamic text is HTML-escaped; names are validated slugs. State changes
only happen via POST. A systemd user unit (`psd-auto-web.service`, port 8099)
is installed but **not enabled** — opt in with
`systemctl --user enable --now psd-auto-web`.

### Scheduling with systemd timers

```bash
psd-auto schedule nightly-cleanup "daily"          # OnCalendar=daily
psd-auto schedule backup-home "*-*-* 02:30:00"     # every night at 02:30
psd-auto timers                                    # list psd-auto timers
psd-auto unschedule backup-home                    # remove
```

Creates `psd-auto-<name>.service`/`.timer` user units with `Persistent=true`
(missed runs fire after boot) using your `psd-auto` on PATH.

## Writing your own automations

User specs live in `~/.config/psd-auto/automations/*.json` (override with
`PSD_AUTO_CONFIG`). Schema:

```json
{
  "name": "nightly-vacuum",            // [a-z0-9-_], 2–64 chars
  "description": "Vacuum the journal and report freed space.",
  "category": "maintenance",
  "timeout": 300,                      // seconds, whole run; per-step timeout also allowed
  "requires_sudo": false,
  "params": {"days": "14"},            // optional; override with --param days=7
  "steps": [
    {"argv": ["journalctl", "--vacuum-time={days}d"], "desc": "vacuum journal"},
    {"shell": "journalctl --disk-usage", "desc": "show size", "allow_failure": true},
    {"http_get": "https://status.example.com/ping", "expect": "ok", "allow_failure": true}
  ]
}
```

Step kinds: `argv` (no shell, preferred) · `shell` (needs quoting/pipes) ·
`http_get` (with optional `expect` substring). Modifiers per step: `sudo`,
`allow_failure`, `timeout`, `desc`. `_source:"builtin"` specs can be shadowed
by a user spec of the same name — copy one from `automations/` and edit.

Validation rejects: empty/oversized steps, bad names, unknown keys at the top
level, `sudo` + interactive-only commands without `--yes` semantics, and a
forbidden-pattern list (raw block devices, `rm -rf /`, pipe-to-shell, fork
bombs, `poweroff`, …). `psd-auto add --file` and the web form run the same
validator, and so does `add --ai`.

## CLI reference

```
psd-auto list [--json]              list automations (built-in + user + native)
psd-auto info <name>                spec details + recent runs
psd-auto run <name> [--dry-run] [--param k=v] [--yes] [--non-interactive]
                    [--force] [--json]
psd-auto add                        interactive wizard
psd-auto add --ai "description"     AI writes the spec (validated)
psd-auto add --file spec.json       import + validate a spec from disk
psd-auto remove <name> [--yes]      delete a user automation (built-ins protected)
psd-auto logs [name] [--tail N]     run history
psd-auto status                     machine + automation + recent-run summary
psd-auto doctor                     full read-only health report
psd-auto web [--port N] [--host IP] local dashboard (default 127.0.0.1:8099)
psd-auto ask "question"             AI Q&A about this machine
psd-auto explain <name>             AI diagnoses the last failure
psd-auto schedule <name> "<cal>"    systemd timer (OnCalendar syntax)
psd-auto unschedule <name> / timers manage timers
psd-auto init / version / help [ai]
```

Exit codes: `0` ok · `1` runtime failure · `2` usage · `3` no such automation ·
`4` validation failed · `5` AI not configured.

## Environment variables

| Variable | Purpose |
|---|---|
| `PSD_AI_API_KEY` / `OPENAI_API_KEY` | AI key — enables `--ai`, `ask`, `explain`, web AI |
| `PSD_AI_BASE_URL` | default `https://api.openai.com/v1` (any OpenAI-compatible endpoint) |
| `PSD_AI_MODEL` | default `gpt-4o-mini` |
| `PSD_AUTO_CONFIG` | override `~/.config/psd-auto` |
| `PSD_AUTO_STATE` | override `~/.local/state/psd-auto` (logs, locks, run history) |
| `PSD_AUTO_ROOT` | install root (automations/) — set by the launcher |
| `PSD_AUTO_WEB_TOKEN` / `PSD_AUTO_WEB_HOST` | dashboard token / bind address |
| `PSD_AUTO_PROJECTS` | where `repo-status` looks for git repos (default `~/Projects`) |

## Logs and state

* `~/.local/state/psd-auto/logs/<name>-<ts>.log` — full per-step output
* `~/.local/state/psd-auto/logs/runs.jsonl` — one JSON line per run (powers `logs`, badges, `status`)
* `~/.local/state/psd-auto/locks/` — per-automation lock files
* `~/.config/psd-auto/config.json` — optional `ai_base_url` / `ai_model`
* `~/.config/psd-auto/automations/` — your specs

## Repository layout

```
fedora-automation/          this project
  src/*.psd                 the tool itself (PSD source; main.psd is the entry)
  automations/*.json        10 built-in declarative specs
  psd-auto                  dev launcher (works from a checkout)
  install.sh                installer (user, PREFIX=, uninstall, purge)
  tests/run_tests.sh        end-to-end smoke suite (hermetic, safe on any host)
  build/psd-auto            AOT binary (git-ignored; produced by install.sh)
psd-language/               the vendored PSD language + compiler (LLVM backend)
scratch/                    local experiments (git-ignored)
```

## Development

```bash
# build the native binary
PYTHONPATH=../psd-language ../.venv/bin/python -m psd build src/main.psd -o build/psd-auto

# run the smoke suite (builds if needed; uses a throwaway temp config/state)
./tests/run_tests.sh

# the PSD compiler's own suite (77 tests incl. AddressSanitizer mode)
cd ../psd-language && PSD_SANITIZE=1 ../.venv/bin/python tests/run_tests.py

# every PSD source file must stay `psd check` clean
for f in src/*.psd; do PYTHONPATH=../psd-language ../.venv/bin/python -m psd check "$f"; done
```

Design notes: the engine (`engine.psd`) interprets specs step by step with
`try_*` envelopes so one failing step never kills the report unless it is the
payload step; native automations (`native.psd`) are compiled in; the web UI
(`webui.psd`) is a server-rendered, dependency-free HTTP app built on the PSD
`std/web.psd` server.

## Honest limitations

* Built and smoke-tested on a Debian 12 container (the development sandbox),
  **not on a real Fedora 45 box**. Every Fedora-specific probe
  (`dnf`/`firewall-cmd`/`sestatus`/`fwupdmgr`/`rpm`) is written to degrade to
  a labeled warning instead of crashing when the tool is absent — but on a
  fresh Fedora 45 install you should run `psd-auto doctor` and
  `psd-auto run <x> --dry-run` first, and review any `shell` step you
  author or AI-generates.
* The web dashboard is plain HTTP for trusted local networks. Do not expose it
  to the internet; put it behind a TLS reverse proxy if you must reach it
  remotely.
* `system-update` and `disk-cleanup` genuinely change the system (with sudo,
  after confirmation, after a dry-run preview). Everything else is read-only
  by default; `tmp-report` deletes nothing; `firmware-check` never applies
  firmware updates.
* PSD is a young language; the toolchain is vendored at `../psd-language` and
  pinned by content hash in its build cache. Language-level bugfixes made
  along the way (string `!=` codegen, use-after-free in HTTP bodies,
  unreachable-host panic, recursive `make_dir`) ship in this repo.

## Safety summary

1. Specs are validated before they are saved — always, including AI output.
2. `argv` steps cannot shell-inject; `shell` steps are visible and flagged.
3. `--dry-run` previews any run; sudo steps confirm interactively.
4. No step ever pipes curl into a shell — the validator refuses it.
5. Locks prevent concurrent runs; failures are logged with full output.
6. The web UI never binds an unauthenticated command-execution surface to a
   routable address.

License: not specified yet — treat as all-rights-reserved for now.
