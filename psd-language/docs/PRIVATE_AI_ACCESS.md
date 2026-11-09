# Give AI models PSD — without making the repo public

Your repo can stay **private forever**. AI models don't need your git
history, your GitHub repo, or your compiler source. They need exactly
two things: **knowledge** (how to write PSD) and, ideally, **a way to
verify** (check/run). This repo gives you three private channels,
from most to least interactive:

| Channel | Best for | Model can check/run? | Repo exposure |
|---|---|---|---|
| **1. MCP server** (`psd mcp`) | Claude Desktop/Code, Cursor, VS Code, local agents | ✅ yes (sandboxed) | none — runs on your machine |
| **2. AI bundle** (`psd bundle`) | ChatGPT, Claude.ai, Gemini (chat UIs) | ❌ knowledge only | one private file you upload |
| **3. Private repo access** | CI agents, Arena-style platforms, your team | ✅ full | read-only, scoped, revocable |

> Rule of thumb: *chat model → bundle. Coding agent on your machine →
> MCP. Automation/CI → scoped private-repo access.* Never make the
> repo public just to please a model, and never paste a GitHub token
> into a chat window.

---

## Channel 1 — MCP server (recommended for coding agents)

The Model Context Protocol is how current AI clients call local tools.
PSD ships a stdlib-only MCP server: the toolchain stays in your private
clone; the model sees seven tools — `psd_guide`, `psd_doc`,
`psd_example`, `psd_template`, `psd_check`, `psd_fmt`, `psd_run` — plus
resources (`psd://guide`, `psd://catalog`, `psd://ai-guide`,
`psd://spec`). The model's loop becomes: read guide → write code →
`psd_check` → apply the error `hint` → `psd_run` → done. That
verification loop is what makes generated PSD actually compile.

Setup (replace `/ABSOLUTE/PATH/TO/psd-language` with your clone):

- **Claude Desktop / Cursor**: copy `integrations/claude_desktop_config.json`
  (Cursor: `integrations/cursor_mcp.json`) into the client's MCP config,
  fix the path, restart the client.
- **VS Code**: use `integrations/vscode_mcp.json` (`.vscode/mcp.json`).
- **Claude Code**: `claude mcp add psd --env PYTHONPATH=/ABSOLUTE/PATH/TO/psd-language -- python3 -m psd mcp`
- **Any stdio MCP client**: command `python3 -m psd mcp` with
  `PYTHONPATH=<repo root>` (or run from inside the clone).
- **Remote machine over SSH** (repo on a server, model client local):
  several clients accept a command, so use
  `ssh your-host "cd /path/to/psd-language && python3 -m psd mcp"` —
  PSD never leaves the server.

Safety (read this): `psd_run` **executes code on your machine**. By
default it runs in a temp dir with a 15 s cap and
`PSD_DISABLE_PROCESS=1` + `PSD_DISABLE_HTTP=1` (capability switches —
defense in depth, not an OS sandbox; file access stays enabled). For a
docs-only server set `PSD_MCP_ALLOW_RUN=0` in the config `env`. Only
pass `"sandbox": false` to `psd_run` for code you trust. Never expose
this server on a network port — MCP stdio has no authentication; for
team/remote use, tunnel it (SSH/Tailscale), don't publish it.

## Channel 2 — AI bundle (for ChatGPT / Claude.ai / Gemini chat)

Chat models can't call your tools, so give them the knowledge as one
file, generated from the live sources (catalog + guide + SPEC +
examples — never compiler code, never stale):

```bash
psd bundle                        # dist/psd-ai-bundle.md   (~16k tokens)
psd bundle --minimal              # guide + catalog + 3 examples (~6k tokens)
```

`dist/` is gitignored on purpose: treat the bundle like source code.

- **ChatGPT**: create a *Project* (or a custom GPT) → upload
  `psd-ai-bundle.md` as project/knowledge files → paste
  `integrations/SYSTEM_PROMPT.md` into the project instructions.
- **Claude.ai**: create a *Project* → add the bundle to project
  knowledge → same system prompt as custom instructions.
- **Gemini**: create a *Gem* → upload the bundle → same instructions.
- **API models (your own app)**: send the minimal bundle as (part of)
  the system prompt, or attach it via your provider's file API.

Privacy reality-check: an uploaded bundle is visible to that AI
provider under *their* data policy. Use a plan/workspace with
no-training controls if the language itself is sensitive IP, re-upload
after `psd bundle` whenever the language changes, and don't put
secrets, tokens, or proprietary app code in the bundle (it contains
none by default).

## Channel 3 — Private repo access (for trusted agents & CI)

For agents that work *in* the repo (Arena, GitHub-connected coding
agents, CI runners), keep the GitHub repo **private** and grant scoped
access through the platform's normal auth — never by making it public:

- **GitHub-connected platforms**: connect your GitHub account (OAuth)
  and pick the private repo; only your account's grants apply.
- **A specific bot/CI**: GitHub → Settings → Developer settings →
  *Fine-grained personal access tokens* → **one repository**, 
  *Contents: Read-only*, short expiry — or a read-only **deploy key**.
  Store it as a CI secret / environment variable on the runner.
- **Install on a trusted machine**: 
  `git clone https://github.com/<you>/psd-language.git` after
  authenticating as yourself (`gh auth login`), or
  `pip install git+ssh://git@github.com/<you>/psd-language.git` with an
  SSH key where a Python package is appropriate.

Never paste a PAT into a chat LLM (it can leak into logs/context and
grants more than PSD knowledge). If a token ever leaks: revoke it —
that's the whole point of scoped, short-lived grants.

## Which channel do I actually need?

| Your situation | Use |
|---|---|
| "I chat with ChatGPT/Claude and want PSD answers" | Bundle (2) |
| "I use Claude Code / Cursor and want working, verified code" | MCP (1) |
| "My app calls an LLM API that should emit PSD" | Bundle as system context + your own executor for check/run (mirror the MCP tools) |
| "CI / an agent platform should build & test PSD" | Private repo (3), read-only |
| "Teammates' agents, repo on our server" | MCP over SSH/Tailscale (1) |

## Checklist before you share anything

- [ ] Repo still private; bundle regenerated (`psd bundle`) and *not* committed (`dist/` ignored).
- [ ] MCP config uses an absolute path and, for docs-only use, `PSD_MCP_ALLOW_RUN=0`.
- [ ] No token in any chat, prompt, bundle, or config committed to git.
- [ ] Any PAT/deploy key is single-repo, read-only, and short-lived.
- [ ] AI workspace/project set to the provider's no-training option if PSD is sensitive IP.
