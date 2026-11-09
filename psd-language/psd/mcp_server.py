"""PSD as an MCP server — give AI models PSD *without* publishing the repo.

Run:  python3 -m psd mcp        (stdio; point your AI client at this)

How it works: the PSD toolchain stays on your machine (private clone).
The AI client (Claude Desktop/Code, Cursor, VS Code, ...) launches this
server locally and the model gets PSD through *tools* — it can read the
guide/catalog, get canonical examples, type-check code, format it, and
run it in a sandbox. The model never needs git access, a GitHub token,
or a public repo; it only sees the tool results you allow.

Protocol: MCP over stdio, newline-delimited JSON-RPC 2.0 (2024-11-05),
implemented with the Python standard library only.

Tools:
  psd_guide    one-screen quick reference (rules + skeleton + signatures)
  psd_doc      signature/description for one function, or the full catalog
  psd_example  canonical example programs (list with name="")
  psd_template project skeleton source (cli|api|web|train|agent)
  psd_check    type-check PSD code -> {ok, errors:[{line,col,message,hint}]}
  psd_fmt      canonical formatting
  psd_run      compile + run in a temp sandbox (see safety below)

Resources: psd://guide, psd://catalog, psd://ai-guide, psd://spec

Safety: psd_run executes code ON YOUR MACHINE.
  - Runs in a fresh temp dir, 15s max timeout, output capped.
  - sandbox=true (default) sets PSD_DISABLE_PROCESS=1 + PSD_DISABLE_HTTP=1
    (capability switches, not an OS sandbox — see SPEC.md). File access
    stays on so try_read_file/write patterns work inside reach of the FS;
    pass sandbox=false only for code you trust, or disable runs entirely
    with PSD_MCP_ALLOW_RUN=0 in the server environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

PROTOCOL = "2024-11-05"
_PKG = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_PKG)
_MAX_OUT = 20000


def _text(obj) -> dict:
    if not isinstance(obj, str):
        obj = json.dumps(obj, indent=2)
    return {"content": [{"type": "text", "text": obj[:_MAX_OUT]}]}


def _err(msg: str) -> dict:
    out = _text({"ok": False, "error": msg})
    out["isError"] = True
    return out


# ---- tool implementations ---------------------------------------------------

def _tool_guide(args: dict) -> dict:
    from . import catalog
    return _text(catalog.render_guide())


def _tool_doc(args: dict) -> dict:
    from . import catalog
    name = (args.get("name") or "").strip()
    if not name:
        return _text(catalog.as_json())
    hit = catalog.find(name)
    if hit is None:
        import difflib
        names = [b[0] for b in catalog.BUILTINS] + [
            f[0] for fns in catalog.STDLIB.values() for f in fns]
        close = difflib.get_close_matches(name, names, n=3)
        return _err(f"no such function '{name}'. close: {close}. "
                    f"Call psd_doc with no name for the full catalog.")
    return _text(hit)


def _examples_dir() -> str:
    return os.path.join(_REPO, "examples")


def _tool_example(args: dict) -> dict:
    name = (args.get("name") or "").strip()
    d = _examples_dir()
    if not os.path.isdir(d):
        return _err("examples/ not found next to the psd package")
    names = sorted(f[:-4] for f in os.listdir(d) if f.endswith(".psd"))
    if not name:
        return _text({"examples": names,
                      "usage": "call psd_example with one of these names"})
    if name not in names:
        return _err(f"no example '{name}'. available: {names}")
    with open(os.path.join(d, name + ".psd"), encoding="utf-8") as f:
        return _text(f.read())


def _tool_template(args: dict) -> dict:
    from . import templates
    name = (args.get("name") or "cli").strip()
    if name not in templates.TEMPLATES:
        return _err(f"no template '{name}'. available: {sorted(templates.TEMPLATES)}")
    return _text(templates.render(name, "myapp"))


def _check_code(code: str) -> dict:
    """Type-check a PSD source string; returns the check --json payload."""
    from .checker import CheckError
    from .cli import _check_json_error, frontend
    from .lexer import LexError
    from .loader import LoaderError
    from .parser import ParseError
    with tempfile.TemporaryDirectory(prefix="psd-mcp-") as td:
        path = os.path.join(td, "main.psd")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        lines = code.splitlines(True)
        try:
            frontend(path)
        except (LexError, ParseError) as e:
            return {"ok": False, "errors": [_check_json_error(
                "main.psd", str(e), getattr(e, "line", None),
                getattr(e, "col", None), lines)]}
        except LoaderError as e:
            return {"ok": False, "errors": [_check_json_error(
                "main.psd", str(e), source_lines=lines)]}
        except CheckError as e:
            return {"ok": False, "errors": [
                _check_json_error("main.psd", line, source_lines=lines)
                for line in e.errors]}
    return {"ok": True, "errors": []}


def _tool_check(args: dict) -> dict:
    code = args.get("code")
    if not isinstance(code, str) or not code.strip():
        return _err("argument 'code' (PSD source string) is required")
    result = _check_code(code)
    out = _text(result)
    if not result["ok"]:
        out["isError"] = True
    return out


def _tool_fmt(args: dict) -> dict:
    code = args.get("code")
    if not isinstance(code, str):
        return _err("argument 'code' (PSD source string) is required")
    from .fmt import format_source
    return _text({"formatted": format_source(code)})


def _tool_run(args: dict) -> dict:
    if os.environ.get("PSD_MCP_ALLOW_RUN", "1") in ("0", "false"):
        return _err("psd_run is disabled on this server (PSD_MCP_ALLOW_RUN=0)")
    code = args.get("code")
    if not isinstance(code, str) or not code.strip():
        return _err("argument 'code' (PSD source string) is required")
    check = _check_code(code)
    if not check["ok"]:
        out = _text({"ok": False, "stage": "check", "errors": check["errors"]})
        out["isError"] = True
        return out
    timeout = args.get("timeout_sec", 15)
    try:
        timeout = max(1, min(int(timeout), 30))
    except (TypeError, ValueError):
        timeout = 15
    sandbox = args.get("sandbox", True)
    prog_args = args.get("args") or []
    if not isinstance(prog_args, list):
        return _err("argument 'args' must be a list of strings")
    with tempfile.TemporaryDirectory(prefix="psd-mcp-run-") as td:
        path = os.path.join(td, "main.psd")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        env = dict(os.environ)
        root = _REPO
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        if sandbox:
            env["PSD_DISABLE_PROCESS"] = "1"
            env["PSD_DISABLE_HTTP"] = "1"
        try:
            r = subprocess.run(
                [sys.executable, "-m", "psd", "run", "--no-cache", path]
                + [str(a) for a in prog_args],
                capture_output=True, text=True, cwd=td, env=env,
                timeout=timeout)
        except subprocess.TimeoutExpired:
            return _err(f"program timed out after {timeout}s")
    return _text({"ok": r.returncode == 0, "exit_code": r.returncode,
                  "stdout": r.stdout[:_MAX_OUT], "stderr": r.stderr[:_MAX_OUT],
                  "sandbox": bool(sandbox)})


TOOLS = [
    {"name": "psd_guide", "description": "PSD quick reference: 10 rules, skeleton, every builtin signature. Read this first.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "psd_doc", "description": "Signature + description for one PSD builtin/stdlib function (name), or the full catalog (no name).",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}}},
    {"name": "psd_example", "description": "Canonical PSD example programs. Empty name lists them; a name returns its source.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}}},
    {"name": "psd_template", "description": "Project skeleton source for cli|api|web|train|agent — start from compiling code.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "default": "cli"}}}},
    {"name": "psd_check", "description": "Type-check PSD code. Returns {ok, errors:[{line,col,message,hint,snippet}]} — fix using hint, then re-check.",
     "inputSchema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "psd_fmt", "description": "Canonical formatting for PSD code.",
     "inputSchema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "psd_run", "description": "Compile + run PSD code in a temp sandbox and return stdout/stderr/exit_code. sandbox=true (default) disables process+HTTP builtins.",
     "inputSchema": {"type": "object", "properties": {
         "code": {"type": "string"},
         "args": {"type": "array", "items": {"type": "string"}},
         "timeout_sec": {"type": "integer", "default": 15},
         "sandbox": {"type": "boolean", "default": True}}, "required": ["code"]}},
]

_HANDLERS = {"psd_guide": _tool_guide, "psd_doc": _tool_doc,
             "psd_example": _tool_example, "psd_template": _tool_template,
             "psd_check": _tool_check, "psd_fmt": _tool_fmt, "psd_run": _tool_run}

RESOURCES = [
    ("psd://guide", "PSD quick reference", "text/plain"),
    ("psd://catalog", "PSD builtin/stdlib catalog (JSON)", "application/json"),
    ("psd://ai-guide", "PSD AI Guide (full writing guide)", "text/markdown"),
    ("psd://spec", "PSD language specification", "text/markdown"),
]


def _read_resource(uri: str) -> str:
    from . import catalog
    if uri == "psd://guide":
        return catalog.render_guide()
    if uri == "psd://catalog":
        return json.dumps(catalog.as_json(), indent=2)
    if uri == "psd://ai-guide":
        path = os.path.join(_REPO, "AI_GUIDE.md")
    elif uri == "psd://spec":
        path = os.path.join(_REPO, "SPEC.md")
    else:
        raise KeyError(uri)
    with open(path, encoding="utf-8") as f:
        return f.read()[:200000]


# ---- JSON-RPC plumbing --------------------------------------------------------

def _result(req_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _failure(req_id, code: int, msg: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": msg}}


def handle(msg: dict) -> dict | None:
    method = msg.get("method", "")
    req_id = msg.get("id")
    params = msg.get("params") or {}
    if req_id is None:                       # notification: never respond
        return None
    if method == "initialize":
        return _result(req_id, {
            "protocolVersion": params.get("protocolVersion", PROTOCOL),
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": "psd-language", "version": "0.6"}})
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": TOOLS})
    if method == "tools/call":
        fn = _HANDLERS.get(params.get("name", ""))
        if fn is None:
            return _failure(req_id, -32602, f"unknown tool '{params.get('name')}'")
        try:
            return _result(req_id, fn(params.get("arguments") or {}))
        except Exception as e:              # a tool bug must not kill the server
            print(f"psd mcp: tool error: {e}", file=sys.stderr)
            return _result(req_id, _err(f"internal error: {e}"))
    if method == "resources/list":
        return _result(req_id, {"resources": [
            {"uri": u, "name": n, "mimeType": m} for (u, n, m) in RESOURCES]})
    if method == "resources/read":
        uri = params.get("uri", "")
        try:
            text = _read_resource(uri)
        except (KeyError, OSError):
            return _failure(req_id, -32602, f"unknown resource '{uri}'")
        mime = next((m for (u, _n, m) in RESOURCES if u == uri), "text/plain")
        return _result(req_id, {"contents": [
            {"uri": uri, "mimeType": mime, "text": text}]})
    return _failure(req_id, -32601, f"method not found: {method}")


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, list):            # batch: answer each request
            out = [r for r in (handle(m) for m in msg) if r is not None]
            if out:
                stdout.write(json.dumps(out) + "\n")
                stdout.flush()
            continue
        resp = handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
