#!/usr/bin/env python3
"""PSD test suite.

  tests/cases/<name>.psd + <name>.expected        -> run, compare stdout
  tests/errors/<name>.psd + <name>.expected_err    -> compile error, stderr match
  tests/runtime_errors/<name>.psd + <name>.runtime_err
                                                  -> runs, exits non-zero,
                                                     stderr contains the message
  Optional per-test files:
  <name>.args   -> command line arguments for the program
  <name>.env    -> KEY=VALUE lines added to the environment

Usage:  python3 tests/run_tests.py
Env:    PSD_SANITIZE=1  -> build with AddressSanitizer (leak/UAF detection)
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PSD = [sys.executable, "-m", "psd"]
ENV = dict(os.environ, PYTHONPATH=ROOT)
sys.path.insert(0, ROOT)          # allow `import psd...` in this harness


def run_program(path):
    args_file = path[:-4] + ".args"
    env_file = path[:-4] + ".env"
    extra = []
    env = dict(ENV)
    if os.path.exists(args_file):
        extra = open(args_file).read().split()
    if os.path.exists(env_file):
        for line in open(env_file):
            line = line.strip()
            if line and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return subprocess.run(PSD + ["run", path] + extra, capture_output=True,
                          text=True, cwd=ROOT, env=env, timeout=120)


def check_success(path, expected_path):
    r = run_program(path)
    want = open(expected_path).read()
    if r.returncode != 0:
        return False, f"exit {r.returncode}\nstderr: {r.stderr}"
    if r.stdout != want:
        return False, f"stdout mismatch\n--- got ---\n{r.stdout}\n--- want ---\n{want}"
    return True, ""


def check_compile_error(path, expected_path):
    r = run_program(path)
    needle = open(expected_path).read().strip()
    if r.returncode == 0:
        return False, "expected compile error, but it succeeded"
    if needle not in (r.stderr + r.stdout):
        return False, f"error message lacks {needle!r}\ngot: {r.stderr}"
    return True, ""


def check_runtime_error(path, expected_path):
    r = run_program(path)
    needle = open(expected_path).read().strip()
    if r.returncode == 0:
        return False, f"expected runtime error, but it succeeded:\n{r.stdout}"
    if "runtime error" not in r.stderr and "error" not in r.stderr:
        return False, f"expected a runtime error message on stderr, got: {r.stderr!r}"
    if needle not in r.stderr:
        return False, f"error message lacks {needle!r}\ngot: {r.stderr}"
    return True, ""


# ---- documentation validation -------------------------------------------
#
# Every ```psd block in the docs must parse; self-contained programs (with
# fn main and no imports) must also pass the type checker. This keeps the
# README / SPEC / AI_GUIDE examples honest.

DOC_FILES = ("README.md", "SPEC.md", "AI_GUIDE.md")


def check_docs():
    from psd.checker import CheckError, check
    from psd.lexer import LexError
    from psd.parser import ParseError, parse

    problems = []
    total = checked = 0
    for doc in DOC_FILES:
        path = os.path.join(ROOT, doc)
        if not os.path.exists(path):
            continue
        text = open(path).read()
        for m in re.finditer(r"```psd\n(.*?)```", text, re.S):
            block = m.group(1)
            total += 1
            line = text[:m.start()].count("\n") + 1
            tag = f"{doc}:{line}"
            is_program = "fn main" in block
            has_import = re.search(r"^\s*(import|from)\s", block, re.M)
            has_fn = re.search(r"^\s*fn\s", block, re.M)
            try:
                if is_program and not has_import:
                    module = parse(block, tag)
                    check(module)
                    checked += 1
                elif has_fn or has_import:
                    parse(block, tag)               # module form: parse only
                else:
                    parse("fn main() {\n" + block + "\n}", tag)  # fragment
            except (LexError, ParseError, CheckError) as e:
                problems.append(f"{tag}: {e}")
    if problems:
        return False, "\n".join(problems)
    return True, f"{total} snippets validated ({checked} type-checked)"


# ---- web server integration ---------------------------------------------
#
# Runs examples/webapp.psd for real: routing, html escaping, form handling,
# query parsing, content types, 404s. PSD_SERVE_COUNT bounds the run so the
# server exits on its own.

def _fetch(port, request):
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.sendall(request)
    data = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
    s.close()
    return data.decode("utf-8", "replace")


def check_server_integration():
    env = dict(ENV)
    env["PSD_PORT"] = "0"
    env["PSD_SERVE_COUNT"] = "9"
    proc = subprocess.Popen(
        PSD + ["run", "examples/webapp.psd"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT, env=env, text=True)
    try:
        port_line = proc.stdout.readline().strip()
        port = int(port_line)

        xss_body = b"text=hello%20from%20tests%3Cok%3E"
        empty_body = b"other=1"   # missing 'text' key: invalid input, ignored
        reqs = [
            (b"GET / HTTP/1.1\r\nHost: t\r\n\r\n",
             lambda r: ("HTTP/1.1 200" in r
                        and "PSD Notes" in r
                        and "&lt;b&gt;ship&lt;/b&gt;" in r     # seeded note escaped
                        and "<b>ship</b>" not in r)),          # never raw
            (b"POST /add HTTP/1.1\r\nHost: t\r\nContent-Length: "
             + str(len(empty_body)).encode() + b"\r\n\r\n" + empty_body,
             lambda r: ("HTTP/1.1 200" in r
                        and "hello from tests" not in r)),     # invalid input: nothing added
            (b"POST /add HTTP/1.1\r\nHost: t\r\nContent-Length: "
             + str(len(xss_body)).encode() + b"\r\n\r\n" + xss_body,
             lambda r: ("HTTP/1.1 200" in r
                        and "hello from tests&lt;ok&gt;" in r   # decoded + escaped
                        and "<ok>" not in r                     # raw markup never reaches the client
                        and "<script" not in r)),               # no XSS payload leaks through
            (b"GET /style.css HTTP/1.1\r\nHost: t\r\n\r\n",
             lambda r: "text/css" in r and "font-family" in r),
            (b"GET /api/status HTTP/1.1\r\nHost: t\r\n\r\n",
             lambda r: "application/json" in r and '"notes": 3' in r),
            (b"GET /search?q=ship HTTP/1.1\r\nHost: t\r\n\r\n",
             lambda r: "search: ship" in r and "build useful" not in r),
            (b"GET /nope HTTP/1.1\r\nHost: t\r\n\r\n",
             lambda r: "HTTP/1.1 404" in r),
            (b"GET /go HTTP/1.1\r\nHost: t\r\n\r\n",
             lambda r: ("HTTP/1.1 302" in r and "Location: /" in r)),
            (b"GET /api/headers HTTP/1.1\r\nHost: t\r\nX-Test: psd\r\n\r\n",
             lambda r: '"x-test": "psd"' in r),
        ]
        for req, want in reqs:
            resp = _fetch(port, req)
            if not want(resp):
                return False, "unexpected response:\n" + resp[:500]
        rc = proc.wait(timeout=15)
        if rc != 0:
            return False, f"webapp exited with {rc}:\n{proc.stderr.read()[:500]}"

        # bind failure: hold the port from Python, then have PSD try to bind it
        hold = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        hold.bind(("127.0.0.1", 0))
        hold.listen(1)
        busy_port = hold.getsockname()[1]
        env2 = dict(ENV, PSD_PORT=str(busy_port))
        r = subprocess.run(PSD + ["run", "tests/server/bind_fail.psd"],
                           capture_output=True, text=True, cwd=ROOT, env=env2,
                           timeout=60)
        hold.close()
        if "bind failed" not in r.stderr:
            return False, "expected bind failure, got:\n" + r.stderr[:300]
        return True, "9 requests verified (routing, invalid input, escaping, forms, json, 404, redirect, headers) + bind-conflict"
    finally:
        if proc.poll() is None:
            proc.kill()


def check_pool_integration():
    """examples/server_pool.psd: pooled workers, redirect, streamed file,
    request headers. Pool workers exit when their serve budget is met —
    several workers may each need requests, so send generously and only
    require every response type to be observed."""
    env = dict(ENV)
    env["PSD_PORT"] = "0"
    env["PSD_SERVE_COUNT"] = "2"
    with open("/tmp/logo.ppm", "w") as fh:
        fh.write("P3\n1 1\n255\n7 8 9\n")
    proc = subprocess.Popen(
        PSD + ["run", "examples/server_pool.psd"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT, env=env, text=True)
    try:
        port_line = proc.stdout.readline().strip()
        if "serving pool on port" not in port_line:
            return False, "pool server did not start: " + port_line
        port = int(port_line.rsplit(" ", 1)[1])
        # Exactly 8 requests: 4 workers x PSD_SERVE_COUNT=2, so every
        # worker exits on budget and the pool shuts itself down.
        seen_root = seen_redirect = seen_file = False
        plan = ["/", "/go", "/logo.ppm", "/", "/go", "/logo.ppm", "/", "/go"]
        for path in plan:
            r = _fetch(port, f"GET {path} HTTP/1.1\r\nHost: t\r\nX-Test: pool\r\n\r\n".encode())
            seen_root = seen_root or ("PSD pool" in r and "x-test" in r)
            seen_redirect = seen_redirect or ("HTTP/1.1 302" in r and "Location: /" in r)
            seen_file = seen_file or ("image/x-portable-pixmap" in r and "7 8 9" in r)
        if not (seen_root and seen_redirect and seen_file):
            return False, f"pool observations root={seen_root} redirect={seen_redirect} file={seen_file}"
        rc = proc.wait(timeout=15)
        if rc != 0:
            return False, f"pool server exited with {rc}:\n{proc.stderr.read()[:500]}"
        return True, "pool workers + redirect + streamed file + headers verified"
    finally:
        if proc.poll() is None:
            proc.kill()


def check_apk_generation():
    """psd apk: generate an Android project from examples/mobile_app.psd.

    Always asserts the project skeleton (manifest, gradle, MainActivity).
    When a cross-compiler is available (ziglang), also asserts a real
    static-PIE arm64 executable was produced for jniLibs.
    """
    import tempfile
    out_dir = os.path.join(tempfile.mkdtemp(prefix="psd-apk-test-"), "app-android")
    try:
        r = subprocess.run(
            PSD + ["apk", "examples/mobile_app.psd", "--out", out_dir,
                   "--name", "PSD Notes", "--package", "com.psd.notes",
                   "--port", "8090", "--abi", "arm64-v8a"],
            capture_output=True, text=True, cwd=ROOT, env=ENV, timeout=900)
    except subprocess.TimeoutExpired:
        return False, "psd apk timed out"
    if r.returncode != 0:
        return False, "psd apk failed:\n" + (r.stderr or r.stdout)[-500:]

    def read(rel):
        with open(os.path.join(out_dir, rel)) as fh:
            return fh.read()

    try:
        manifest = read("app/src/main/AndroidManifest.xml")
        gradle = read("app/build.gradle")
        activity = read("app/src/main/java/com/psd/notes/MainActivity.java")
    except OSError as e:
        return False, f"generated project incomplete: {e}"
    if 'android:extractNativeLibs="true"' not in manifest or "INTERNET" not in manifest:
        return False, "manifest lacks extractNativeLibs/INTERNET"
    if "applicationId 'com.psd.notes'" not in gradle or "useLegacyPackaging" not in gradle:
        return False, "gradle lacks applicationId/useLegacyPackaging"
    if "libpsdapp.so" not in activity or "PORT = 8090" not in activity:
        return False, "MainActivity does not launch the PSD binary on port 8090"

    import importlib.util
    if importlib.util.find_spec("ziglang") is None and shutil.which("zig") is None:
        return True, "project skeleton verified (no zig/NDK here: binary skipped)"
    so = os.path.join(out_dir, "app/src/main/jniLibs/arm64-v8a/libpsdapp.so")
    if not os.path.exists(so):
        return False, "zig available but no arm64 binary generated:\n" + r.stdout[-400:]
    with open(so, "rb") as fh:
        head = fh.read(20)
    if head[:4] != b"\x7fELF" or int.from_bytes(head[18:20], "little") != 183 \
            or int.from_bytes(head[16:18], "little") != 3:
        return False, "libpsdapp.so is not an AArch64 PIE executable"
    return True, "android project + real arm64 static-PIE PSD binary verified"
# ---- v0.5 AI-speed tooling ------------------------------------------------
# The generate->check->run loop is only useful if it is fast and the
# toolchain answers questions itself: catalog (psd doc), guide skeletons,
# canonical formatting, scaffolds that compile, and a build cache that
# never serves stale binaries.

def check_catalog():
    import json as _json
    from psd import catalog
    from psd.checker import BUILTIN_NAMES, check
    from psd.parser import parse
    cat = {b[0] for b in catalog.BUILTINS}
    if cat != set(BUILTIN_NAMES):
        return False, f"catalog/checker builtin drift: {cat ^ set(BUILTIN_NAMES)}"
    for mod, fns in catalog.STDLIB.items():
        path = os.path.join(ROOT, "psd", mod)
        names = {f.name for f in parse(open(path).read(), path).funcs}
        want = {f[0] for f in fns}
        if names != want:
            return False, f"{mod} catalog drift: {names ^ want}"
    r = subprocess.run(PSD + ["doc", "--json"], capture_output=True, text=True,
                       cwd=ROOT, env=ENV, timeout=60)
    data = _json.loads(r.stdout)
    if len(data["builtins"]) != len(BUILTIN_NAMES):
        return False, "doc --json builtin count mismatch"
    r = subprocess.run(PSD + ["doc", "try_run"], capture_output=True, text=True,
                       cwd=ROOT, env=ENV, timeout=60)
    if "try_run(" not in r.stdout:
        return False, "doc try_run lookup failed:\n" + r.stdout + r.stderr
    check(parse(catalog.SKELETON, "<guide>"))     # guide skeleton must compile
    return True, f"{len(cat)} builtins + stdlib catalog in sync; guide skeleton compiles"


def check_fmt():
    from psd.fmt import format_source
    from psd.parser import parse
    import glob as _glob
    files = (_glob.glob(os.path.join(ROOT, "examples", "*.psd")) +
             _glob.glob(os.path.join(ROOT, "psd", "std", "*.psd")) +
             _glob.glob(os.path.join(ROOT, "tests", "cases", "*.psd")))
    for path in files:
        src = open(path).read()
        once = format_source(src)
        if format_source(once) != once:
            return False, f"fmt not idempotent: {path}"
        parse(once, path)                          # formatted code still parses
    return True, f"fmt idempotent + parse-safe on {len(files)} files"


def check_scaffold():
    import tempfile
    for tpl in ("cli", "api", "web", "train", "agent"):
        with tempfile.TemporaryDirectory() as td:
            proj = os.path.join(td, "proj")
            r = subprocess.run(PSD + ["new", proj, "--template", tpl],
                               capture_output=True, text=True, cwd=ROOT, env=ENV,
                               timeout=60)
            if r.returncode != 0:
                return False, f"new {tpl}: {r.stderr}"
            r = subprocess.run(PSD + ["check", os.path.join(proj, "main.psd")],
                               capture_output=True, text=True, cwd=ROOT, env=ENV,
                               timeout=60)
            if r.returncode != 0:
                return False, f"template {tpl} does not check:\n{r.stderr}"
    return True, "all 5 templates scaffold + type-check"


def check_cache():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        env = dict(ENV, PSD_CACHE_DIR=os.path.join(td, "cache"))
        src = os.path.join(td, "hi.psd")
        open(src, "w").write('fn main() {\n    print("v1")\n}\n')
        for want in ("v1", "v1"):
            r = subprocess.run(PSD + ["run", src], capture_output=True, text=True,
                               cwd=ROOT, env=env, timeout=120)
            if r.stdout.strip() != want:
                return False, f"cache run: got {r.stdout!r} want {want!r} ({r.stderr})"
        bins = [f for f in os.listdir(env["PSD_CACHE_DIR"]) if f.startswith("bin-")]
        if not bins:
            return False, "no cached binary written"
        open(src, "w").write('fn main() {\n    print("v2")\n}\n')
        r = subprocess.run(PSD + ["run", src], capture_output=True, text=True,
                           cwd=ROOT, env=env, timeout=120)
        if r.stdout.strip() != "v2":
            return False, f"stale cache served: {r.stdout!r}"
    return True, "warm run hits cache; edited source recompiles (no stale binaries)"


# ---- private AI access: MCP server + bundle --------------------------------
# The repo stays private; models get tools (MCP) or knowledge (bundle).
# These tests speak real MCP JSON-RPC over stdio and verify the sandbox.

def _mcp_session(messages, env_extra=None):
    env = dict(ENV)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen(PSD + ["mcp"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=ROOT, env=env)
    out, _ = proc.communicate(
        "".join(json.dumps(m) + "\n" for m in messages), timeout=120)
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def check_mcp():
    import json as _json  # noqa: F401  (json already imported? ensure local)
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "psd_check", "arguments": {
             "code": 'fn main() {\n    print(1 + 2)\n}\n'}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "psd_check", "arguments": {
             "code": 'fn main() {\n    print(nope)\n}\n'}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "psd_run", "arguments": {
             "code": 'fn main() {\n    print(6 * 7)\n}\n'}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
         "params": {"name": "psd_run", "arguments": {
             "code": 'fn main() {\n    print(capture(["echo", "x"]))\n}\n'}}},
        {"jsonrpc": "2.0", "id": 7, "method": "resources/read",
         "params": {"uri": "psd://catalog"}},
    ]
    try:
        resp = _mcp_session(msgs)
    except Exception as e:
        return False, f"mcp session failed: {e}"
    by_id = {m.get("id"): m for m in resp}
    if by_id[1]["result"]["serverInfo"]["name"] != "psd-language":
        return False, "bad initialize response"
    tools = {t["name"] for t in by_id[2]["result"]["tools"]}
    if tools != {"psd_guide", "psd_doc", "psd_example", "psd_template",
                 "psd_check", "psd_fmt", "psd_run"}:
        return False, f"tool set drift: {tools}"
    ok_check = json.loads(by_id[3]["result"]["content"][0]["text"])
    if not ok_check["ok"]:
        return False, f"psd_check rejected valid code: {ok_check}"
    bad_check = json.loads(by_id[4]["result"]["content"][0]["text"])
    if bad_check["ok"] or not bad_check["errors"][0].get("hint"):
        return False, f"psd_check lacks structured hint: {bad_check}"
    ran = json.loads(by_id[5]["result"]["content"][0]["text"])
    if not ran["ok"] or ran["stdout"].strip() != "42":
        return False, f"psd_run wrong: {ran}"
    blocked = json.loads(by_id[6]["result"]["content"][0]["text"])
    if "disabled" not in blocked.get("stderr", ""):
        return False, f"sandbox did not block process execution: {blocked}"
    cat = json.loads(by_id[7]["result"]["contents"][0]["text"])
    if cat["language"] != "PSD" or not cat["builtins"]:
        return False, "catalog resource broken"
    off = _mcp_session([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "psd_run", "arguments": {
                             "code": "fn main() { print(1) }"}}}],
                        env_extra={"PSD_MCP_ALLOW_RUN": "0"})
    txt = off[0]["result"]["content"][0]["text"]
    if "disabled" not in txt:
        return False, "PSD_MCP_ALLOW_RUN=0 not honored"
    return True, "MCP handshake, 7 tools, check/run + sandbox verified over stdio"


def check_bundle():
    full = subprocess.run(PSD + ["bundle", "--stdout"], capture_output=True,
                          text=True, cwd=ROOT, env=ENV, timeout=60)
    mini = subprocess.run(PSD + ["bundle", "--minimal", "--stdout"],
                          capture_output=True, text=True, cwd=ROOT, env=ENV,
                          timeout=60)
    for tag, r in (("full", full), ("minimal", mini)):
        if r.returncode != 0:
            return False, f"bundle {tag} failed: {r.stderr}"
        for marker in ("PSD", "psd_check", "fn main", "try_run"):
            if marker not in r.stdout:
                return False, f"bundle {tag} missing {marker!r}"
    if not (0 < len(mini.stdout) < len(full.stdout)):
        return False, "minimal bundle is not smaller than full"
    if "typedef struct psd_str" in full.stdout or "def check_expr" in full.stdout:
        return False, "bundle leaks compiler source code"
    return True, (f"bundle full ~{len(full.stdout) // 4} tok, "
                  f"minimal ~{len(mini.stdout) // 4} tok, no compiler source")


def main():
    passed = failed = 0
    case_dir = os.path.join(ROOT, "tests", "cases")
    err_dir = os.path.join(ROOT, "tests", "errors")
    rt_dir = os.path.join(ROOT, "tests", "runtime_errors")

    tests = []
    for f in sorted(os.listdir(case_dir)):
        if f.endswith(".psd"):
            tests.append((os.path.join(case_dir, f), check_success,
                          os.path.join(case_dir, f[:-4] + ".expected")))
    for f in sorted(os.listdir(err_dir)):
        if f.endswith(".psd"):
            tests.append((os.path.join(err_dir, f), check_compile_error,
                          os.path.join(err_dir, f[:-4] + ".expected_err")))
    if os.path.isdir(rt_dir):
        for f in sorted(os.listdir(rt_dir)):
            if f.endswith(".psd"):
                tests.append((os.path.join(rt_dir, f), check_runtime_error,
                              os.path.join(rt_dir, f[:-4] + ".runtime_err")))

    for path, checker, expected in tests:
        name = os.path.relpath(path, ROOT)
        try:
            good, msg = checker(path, expected)
        except subprocess.TimeoutExpired:
            good, msg = False, "TIMEOUT"
        if good:
            passed += 1
            print(f"  ok   {name}")
        else:
            failed += 1
            print(f"  FAIL {name}\n{msg}")

    for name, fn in (("docs (README/SPEC/AI_GUIDE snippets)", check_docs),
                     ("catalog (psd doc/guide in sync)", check_catalog),
                     ("fmt (canonical, idempotent)", check_fmt),
                     ("scaffold (psd new templates)", check_scaffold),
                     ("cache (fast warm runs, no stale)", check_cache),
                     ("mcp (private AI tools over stdio)", check_mcp),
                     ("bundle (private AI knowledge file)", check_bundle),
                     ("integration (examples/webapp.psd server)", check_server_integration),
                     ("integration (examples/server_pool.psd pool)", check_pool_integration),
                     ("integration (psd apk android project)", check_apk_generation)):
        try:
            good, msg = fn()
        except subprocess.TimeoutExpired:
            good, msg = False, "TIMEOUT"
        if good:
            passed += 1
            print(f"  ok   {name} — {msg}")
        else:
            failed += 1
            print(f"  FAIL {name}\n{msg}")

    san = " (with AddressSanitizer)" if os.environ.get("PSD_SANITIZE") == "1" else ""
    print(f"\n{passed} passed, {failed} failed{san}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
