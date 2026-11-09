# psd-automation

**psd-auto** — an AI-powered workstation automation tool for **Fedora Linux 45**,
written in the **PSD language** and compiled to a native binary.

Describe what you want in plain English and get a validated, dry-runnable
automation — or run one of the built-in, read-mostly system automations
(updates, security audit, disk cleanup, backups, network/health checks).
Everything runs locally; the AI is bring-your-own-key and can point at a
fully local Ollama instance.

```bash
cd fedora-automation
./install.sh          # checks deps, builds the native binary, installs under ~/.local
psd-auto doctor       # read-only health report
psd-auto web          # local dashboard on http://127.0.0.1:8099/
```

📖 Full documentation: **[fedora-automation/README.md](fedora-automation/README.md)**
(install, built-in automations, AI configuration, web dashboard, scheduling,
spec format, safety model, development).

## Repository layout

| Path | What it is |
|---|---|
| [`fedora-automation/`](fedora-automation/) | **the product** — psd-auto, written in PSD (`src/*.psd`), 10 built-in spec automations + 3 native ones, installer, smoke tests |
| [`psd-language/`](psd-language/) | the **PSD language** toolchain vendored for standalone builds — lexer/parser/checker/LLVM AOT codegen, C runtime, test suite (`tests/run_tests.py`, 77 tests), docs ([SPEC](psd-language/SPEC.md) · [AI_GUIDE](psd-language/AI_GUIDE.md)) |

PSD is a small, typed, LLVM-compiled language designed so both humans and AI
models can write it reliably. psd-auto is its reference application.

## Safety in one minute

Automations are declarative specs validated before saving (name rules, step
shapes, forbidden patterns — no `rm -rf /`, no pipe-to-shell, no raw block-device
writes). `argv` steps need no shell and cannot inject. `--dry-run` previews any
run; sudo steps confirm interactively. Every run is logged with full output.
The web dashboard never binds an unauthenticated command-execution surface to a
routable address. See the [safety summary](fedora-automation/README.md#safety-summary).

## Status and testing

* `psd-language`: 77/77 tests (incl. AddressSanitizer mode) — `cd psd-language && PSD_SANITIZE=1 ../.venv/bin/python tests/run_tests.py`
* `psd-auto`: smoke suite — `cd fedora-automation && ./tests/run_tests.sh`
* Honest limitation: developed and verified on a Debian 12 container; every
  Fedora-specific probe degrades to a labeled warning when the tool is
  missing, but please run `psd-auto doctor` and `--dry-run` on a real Fedora
  45 box before relying on the mutating automations.
