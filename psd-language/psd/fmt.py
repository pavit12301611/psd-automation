"""Canonical PSD formatting (`psd fmt`).

Formatting is never a reason code fails for an AI (or a human): `psd fmt`
normalizes the layout so generated code always looks the same and diffs
stay minimal. The formatter is deliberately conservative — it only fixes
indentation (4 spaces per block), trailing whitespace, blank-line runs,
and the final newline. It never rewrites a line's contents, so comments
and string literals survive untouched.

Brace counting ignores `{`/`}` inside strings and comments (PSD strings
are single-line, so this is exact).
"""

from __future__ import annotations


def _scan(line: str) -> tuple[int, int, int]:
    """Return (opens, closes, paren_delta) for one line, string/comment aware."""
    opens = closes = parens = 0
    i = 0
    in_str = False
    while i < len(line):
        ch = line[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
        elif ch == "#" or (ch == "/" and i + 1 < len(line) and line[i + 1] == "/"):
            break                      # comment: rest of line is not code
        elif ch == "{":
            opens += 1
        elif ch == "}":
            closes += 1
        elif ch in "([":
            parens += 1
        elif ch in ")]":
            parens -= 1
        i += 1
    return opens, closes, parens


def format_source(src: str) -> str:
    out: list[str] = []
    depth = 0          # { } block depth
    paren = 0          # ( [ continuation depth
    blank_run = 0
    for raw in src.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            blank_run += 1
            if blank_run <= 1 and out:
                out.append("")
            continue
        blank_run = 0
        opens, closes, pdelta = _scan(line)
        dedent = 1 if stripped.startswith("}") else 0
        level = max(depth - dedent, 0) + (1 if paren > 0 else 0)
        out.append("    " * level + stripped)
        depth = max(depth + opens - closes, 0)
        paren = max(paren + pdelta, 0)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n" if out else ""
