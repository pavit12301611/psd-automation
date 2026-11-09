"""AI bundle generator (`psd bundle`).

Builds ONE self-contained markdown file that teaches an AI model PSD —
for uploading to a *private* ChatGPT Project / Claude Project / custom
GPT knowledge base / Gemini Gem, so models can write PSD for you without
the repo ever being public.

  psd bundle                      # -> dist/psd-ai-bundle.md (full)
  psd bundle --minimal            # guide + catalog + 3 examples (small contexts)
  psd bundle -o out.md --stdout   # also print it

The bundle is generated from the live sources (catalog, AI_GUIDE, SPEC,
examples), so it cannot drift from the language. It deliberately
contains NO compiler source — only what a model needs to *write* PSD.
Keep the generated file private like you'd keep the repo private.
"""

from __future__ import annotations

import json
import os

_PKG = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_PKG)

_FULL_EXAMPLES = ["hello", "features", "fibonacci", "automation",
                  "gui_dashboard", "train_classifier", "agent_tools"]
_MIN_EXAMPLES = ["hello", "features", "agent_tools"]

_INSTRUCTIONS = """\
# PSD — instructions for the AI model reading this file

PSD is a private, statically-typed, compiled programming language
(Python-like syntax, native binaries). It is NOT Python: follow the
rules below exactly, do not invent syntax or functions, and prefer the
canonical patterns in the examples.

How to work:
1. Write code using ONLY the builtins/stdlib functions in the catalog.
2. If PSD MCP tools are available (psd_guide, psd_doc, psd_example,
   psd_template, psd_check, psd_fmt, psd_run), loop: psd_check, apply
   each error's `hint`, psd_fmt, psd_run — never present code as working
   without a successful check. With only the CLI, use
   `psd check --json`. With neither, mentally type-check first.
3. Match the examples' style: `let` declarations, typed parameters,
   `try_*` envelopes for failure handling, argv-list processes.
"""


def _read(rel: str) -> str:
    path = os.path.join(_REPO, rel)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _examples(names: list[str]) -> str:
    out = []
    for name in names:
        src = _read(os.path.join("examples", name + ".psd"))
        if src:
            out.append(f"### examples/{name}.psd\n\n```psd\n{src.rstrip()}\n```")
    return "\n\n".join(out)


def build_bundle(minimal: bool = False) -> str:
    from . import catalog
    parts = [_INSTRUCTIONS]
    parts.append("## Quick reference (start here)\n\n```text\n"
                 + catalog.render_guide().rstrip() + "\n```")
    parts.append("## Catalog (every callable, machine-verified)\n\n```json\n"
                 + json.dumps(catalog.as_json()) + "\n```")
    if not minimal:
        guide = _read("AI_GUIDE.md")
        if guide:
            parts.append("## Full writing guide (AI_GUIDE.md)\n\n" + guide.rstrip())
        spec = _read("SPEC.md")
        if spec:
            parts.append("## Language specification (SPEC.md)\n\n" + spec.rstrip())
    names = _MIN_EXAMPLES if minimal else _FULL_EXAMPLES
    parts.append(f"## Canonical examples\n\n{_examples(names)}")
    parts.append(
        "## Hard limits (do not invent past these)\n\n"
        "- No classes, exceptions, lambdas, tensors/GPU, WebSockets, TLS, "
        "or string indexing.\n"
        "- Server is sequential HTTP/1.1 on 127.0.0.1; browser is the GUI.\n"
        "- Real ML training beyond small tabular models is delegated to an "
        "external runtime over json files + run([...]).\n")
    return "\n\n".join(parts) + "\n"


def estimate_tokens(text: str) -> int:
    """Rough token count (≈ 4 chars/token) for context-budget decisions."""
    return len(text) // 4
