"""PSD command line driver.

  psd run file.psd        compile + execute (build-cached: ~0.09s warm)
  psd build file.psd      compile to a native binary
  psd check file.psd      type-check only (--json for AI agents)
  psd emit-llvm file.psd  write LLVM IR (.ll) for inspection / clang
  psd fmt file.psd        canonical formatting (-w write, --check verify)
  psd doc [name]          builtin/stdlib catalog (--json for machines)
  psd guide               one-screen quick reference for AI agents
  psd new dir --template  scaffold a compiling project (cli/api/web/train/agent)
  psd cache info|clear    build-cache management
  psd mcp                 give AI models PSD via MCP tools (repo stays private)
  psd bundle              one-file AI bundle for private ChatGPT/Claude Projects
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

try:
    from .checker import CheckError, check
    from .codegen_llvm import generate
    from .lexer import LexError
except ImportError as e:
    if "llvmlite" in str(e):
        print("error: PSD needs the 'llvmlite' package.\n"
              "  python3 -m venv .venv && .venv/bin/pip install llvmlite\n"
              "See README.md for setup instructions.", file=sys.stderr)
        sys.exit(2)
    raise
from .parser import ParseError, parse
from .loader import LoaderError


RUNTIME_C = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime.c")

_LLVM_READY = False


def _init_llvm():
    global _LLVM_READY
    if _LLVM_READY:
        return
    import llvmlite.binding as llvm
    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()
    _LLVM_READY = True


def read_source(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        print(f"error: cannot read {path}: {e.strerror}", file=sys.stderr)
        sys.exit(1)


def frontend(path: str):
    from .loader import load_program
    prog = load_program(path)
    check(prog.module, prog)
    return prog


# ---- build cache -----------------------------------------------------------
# `psd run` in an AI generate->check->run->fix loop must be fast. The C
# runtime and the LLVM compile dominate (~1s); both are pure functions of
# file contents, so binaries are cached under a content hash:
#   ~/.cache/psd-language/bin-<hash>   (override: $PSD_CACHE_DIR)
# A cache hit is a process exec (~50ms). Disable with --no-cache or
# PSD_NO_CACHE=1. Keys cover every source file + the whole toolchain
# (psd/*.py + runtime.c + llvmlite version + sanitize mode), so a stale
# binary can never be served.

def _cache_dir() -> str:
    d = os.environ.get("PSD_CACHE_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "psd-language")
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except OSError:
        d = os.path.join(tempfile.gettempdir(), "psd-language-cache")
        os.makedirs(d, exist_ok=True)
        return d


def _cache_on(args) -> bool:
    if getattr(args, "no_cache", False):
        return False
    return os.environ.get("PSD_NO_CACHE", "") not in ("1", "true")


def _hash_into(h, path: str) -> None:
    try:
        with open(path, "rb") as f:
            h.update(f.read())
    except OSError:
        h.update(b"<missing>")
    h.update(b"\0")


def _toolchain_key() -> str:
    import hashlib
    import llvmlite
    h = hashlib.sha256()
    pkg = os.path.dirname(os.path.abspath(__file__))
    for name in sorted(os.listdir(pkg)):
        if name.endswith(".py"):
            _hash_into(h, os.path.join(pkg, name))
    _hash_into(h, RUNTIME_C)
    h.update(llvmlite.__version__.encode())
    h.update(b"sanitize" if os.environ.get("PSD_SANITIZE") == "1" else b"plain")
    return h.hexdigest()[:24]


def _program_key(prog, toolchain_key: str) -> str:
    import hashlib
    h = hashlib.sha256()
    h.update(toolchain_key.encode())
    for p in prog.source_paths:
        _hash_into(h, p)
    return h.hexdigest()[:24]


def _atomic_copy(src: str, dst: str, executable: bool = False) -> None:
    tmp = f"{dst}.tmp{os.getpid()}"
    shutil.copyfile(src, tmp)
    if executable:
        os.chmod(tmp, 0o755)
    os.replace(tmp, dst)


def _cached_runtime_obj(cc: str, extra: list, toolchain_key: str, td: str) -> str:
    """Compile runtime.c once per toolchain, then reuse the object file."""
    cached = os.path.join(_cache_dir(), f"rt-{toolchain_key}.o")
    if os.path.exists(cached):
        return cached
    out = os.path.join(td, "runtime.o")
    r = subprocess.run([cc, "-O2", "-std=c11", "-c", RUNTIME_C, "-o", out] + extra,
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("error: failed to compile the PSD runtime:\n" + r.stderr, file=sys.stderr)
        sys.exit(1)
    try:
        _atomic_copy(out, cached)
        return cached
    except OSError:
        return out


def compile_to_object(ir_text: str) -> bytes:
    import llvmlite.binding as llvm
    _init_llvm()
    mod = llvm.parse_assembly(ir_text)
    mod.verify()
    triple = llvm.get_default_triple()
    tm = llvm.Target.from_default_triple().create_target_machine(opt=3)
    pto = llvm.create_pipeline_tuning_options()
    pto.speed_level = 3
    pto.loop_vectorization = True
    pto.slp_vectorization = True
    pb = llvm.create_pass_builder(tm, pto)
    pm = pb.getModulePassManager()
    pm.run(mod, pb)
    return tm.emit_object(mod)


def find_cc() -> str:
    for cand in ("cc", "gcc", "clang"):
        path = shutil.which(cand)
        if path:
            return path
    print("error: no C compiler found (need cc/gcc/clang to link)", file=sys.stderr)
    sys.exit(1)


def build_binary(path: str, out_path: str, use_cache: bool = True) -> None:
    exe = ensure_binary(path, use_cache=use_cache)
    if os.path.abspath(exe) != os.path.abspath(out_path):
        # copy to a temp file in the same directory, then rename(2):
        # replacing a running executable with rename works, while an
        # in-place copy onto it fails with ETXTBSY
        tmp = os.path.join(os.path.dirname(out_path) or ".",
                           f".psd-build-{os.getpid()}.tmp")
        shutil.copyfile(exe, tmp)
        os.chmod(tmp, 0o755)
        os.replace(tmp, out_path)


def ensure_binary(path: str, use_cache: bool = True) -> str:
    """Compile `path` (or serve the cached binary) and return the exe path.

    With the cache on, the returned path lives in the cache dir and stays
    valid after this call. With it off, the binary is built in a temp dir
    whose lifetime the caller manages via `_keepalive` below.
    """
    import llvmlite.binding as llvm
    _init_llvm()
    prog = frontend(path)
    module = prog.module

    cached_bin = None
    if use_cache:
        cached_bin = os.path.join(
            _cache_dir(), f"bin-{_program_key(prog, _toolchain_key())}")
        if os.path.exists(cached_bin):
            return cached_bin

    ir_text = generate(module, triple=llvm.get_default_triple())
    obj = compile_to_object(ir_text)

    cc = find_cc()
    sanitize = os.environ.get("PSD_SANITIZE") == "1"
    extra = ["-fsanitize=address", "-fno-omit-frame-pointer"] if sanitize else []
    td_obj = tempfile.TemporaryDirectory(prefix="psd-build-")
    _keepalive.append(td_obj)
    td = td_obj.name
    obj_path = os.path.join(td, "prog.o")
    with open(obj_path, "wb") as f:
        f.write(obj)
    if use_cache:
        rt_obj = _cached_runtime_obj(cc, extra, _toolchain_key(), td)
        out_path = os.path.join(td, "prog")
    else:
        rt_obj = os.path.join(td, "runtime.o")
        r = subprocess.run([cc, "-O2", "-std=c11", "-c", RUNTIME_C, "-o", rt_obj] + extra,
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("error: failed to compile the PSD runtime:\n" + r.stderr, file=sys.stderr)
            sys.exit(1)
        out_path = os.path.join(td, "prog")
    r = subprocess.run([cc, "-O3", obj_path, rt_obj, "-o", out_path, "-lm"] + extra,
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("error: failed to link:\n" + r.stderr, file=sys.stderr)
        sys.exit(1)
    os.chmod(out_path, 0o755)
    if cached_bin is not None:
        try:
            _atomic_copy(out_path, cached_bin, executable=True)
            return cached_bin
        except OSError:
            pass
    return out_path


_keepalive: list = []


def _frontend_errors(args, fn) -> int | None:
    """Run fn(); on a frontend error print it and return the exit code."""
    try:
        fn()
    except (LexError, ParseError) as e:
        print(f"{args.file}:{e}", file=sys.stderr)
        return 1
    except LoaderError as e:
        print(e, file=sys.stderr)
        return 1
    except CheckError as e:
        for line in e.errors:
            print(f"{args.file}: {line}", file=sys.stderr)
        return 1
    return None


def cmd_build(args) -> int:
    out = args.output or os.path.splitext(os.path.basename(args.file))[0]
    rc = _frontend_errors(args, lambda: build_binary(
        args.file, out, use_cache=_cache_on(args)))
    if rc is not None:
        return rc
    print(f"built {out}")
    return 0


def cmd_run(args) -> int:
    holder: dict = {}
    rc = _frontend_errors(args, lambda: holder.setdefault(
        "exe", ensure_binary(args.file, use_cache=_cache_on(args))))
    if rc is not None:
        return rc
    r = subprocess.run([holder["exe"]] + list(getattr(args, "prog_args", []) or []))
    return r.returncode


def cmd_cache(args) -> int:
    d = _cache_dir()
    if args.action == "clear":
        n = 0
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                os.unlink(p)
                n += 1
        print(f"cleared {n} cache file(s) in {d}")
        return 0
    files = [f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))]
    size = sum(os.path.getsize(os.path.join(d, f)) for f in files)
    bins = sum(1 for f in files if f.startswith("bin-"))
    print(f"cache dir: {d}")
    print(f"{len(files)} file(s), {bins} cached program(s), {size / 1e6:.1f} MB")
    return 0


def _hint_for(message: str) -> str | None:
    """One-line, machine-actionable fix for common diagnostics."""
    import re
    m = re.search(r"did you mean '([^']+)'", message)
    if m:
        return f"use '{m.group(1)}' instead"
    m = re.search(r"— use (.+)$", message)
    if m:
        return f"use {m.group(1)}"
    if "booleans are lowercase" in message:
        return "use true / false (lowercase)"
    if "no None" in message:
        return "use json_parse(\"null\") for a JSON null, or a typed empty value"
    rules = [
        ("condition must be bool", "write an explicit comparison, e.g. `if x > 0` (json values may also be tested directly)"),
        ("cannot assign", "convert explicitly (str()/int()/float()) or change the declared type/annotation"),
        ("needs a type annotation", "annotate the declaration, e.g. `let x: [int] = []`"),
        ("json directly", "convert first with int()/float()/str(), do arithmetic, then assign back"),
        ("compound assignment", "read the value, convert, compute, assign — e.g. `j[\"n\"] = int(j[\"n\"]) + 1`"),
        ("must return a value", "add a `return <value>` on every path, or drop the `-> T` annotation"),
        ("undefined variable", "declare it with `let` before use, or check the spelling"),
        ("unknown function", "check the spelling; run `psd doc --json` for the builtin list"),
        ("expected a type", "PSD types: int, float, str, bool, json, [T], {str: T}"),
        ("strings use double quotes", "rewrite with double quotes; add the f prefix for interpolation: f\"hi {name}\""),
        ("has no function", "check the module's function names (`psd doc --json` lists stdlib ones)"),
        ("parameter types are required", "annotate every parameter: `fn f(x: int, s: str)`"),
        ("str indexing is not supported", "use .split()/.contains()/.trim() instead of s[i]"),
        ("list index must be int", "convert the index with int(...)"),
    ]
    for needle, hint in rules:
        if needle in message:
            return hint
    return None


def _check_json_error(file: str, message: str, line=None, col=None,
                      source_lines: list | None = None) -> dict:
    import re
    m = re.search(r"\(line (\d+), col (\d+)\)\s*$", message)
    if m:
        line, col = int(m.group(1)), int(m.group(2))
        message = message[:m.start()].strip()
    if message.startswith("error:"):
        message = message[len("error:"):].strip()
    snippet = None
    if source_lines and line and 1 <= line <= len(source_lines):
        snippet = source_lines[line - 1].rstrip("\n")
    return {"file": file, "line": line, "col": col, "message": message,
            "hint": _hint_for(message), "snippet": snippet}


def cmd_check(args) -> int:
    json_out = getattr(args, "json_out", False)
    source_lines = None
    if json_out:
        try:
            with open(args.file, encoding="utf-8") as f:
                source_lines = f.readlines()
        except OSError:
            source_lines = None
    try:
        frontend(args.file)
    except (LexError, ParseError) as e:
        if json_out:
            import json as _json
            print(_json.dumps({"ok": False, "file": args.file, "errors": [
                _check_json_error(args.file, str(e),
                                  getattr(e, "line", None),
                                  getattr(e, "col", None), source_lines)]}))
        else:
            print(f"{args.file}:{e}", file=sys.stderr)
        return 1
    except LoaderError as e:
        if json_out:
            import json as _json
            print(_json.dumps({"ok": False, "file": args.file, "errors": [
                _check_json_error(args.file, str(e), source_lines=source_lines)]}))
        else:
            print(e, file=sys.stderr)
        return 1
    except CheckError as e:
        if json_out:
            import json as _json
            print(_json.dumps({"ok": False, "file": args.file, "errors": [
                _check_json_error(args.file, line, source_lines=source_lines)
                for line in e.errors]}))
        else:
            for line in e.errors:
                print(f"{args.file}: {line}", file=sys.stderr)
        return 1
    if json_out:
        import json as _json
        print(_json.dumps({"ok": True, "file": args.file, "errors": []}))
    else:
        print("ok")
    return 0


def cmd_emit_llvm(args) -> int:
    import llvmlite.binding as llvm
    _init_llvm()
    try:
        module = frontend(args.file)
    except (LexError, ParseError) as e:
        print(f"{args.file}:{e}", file=sys.stderr)
        return 1
    except LoaderError as e:
        print(e, file=sys.stderr)
        return 1
    except CheckError as e:
        for line in e.errors:
            print(f"{args.file}: {line}", file=sys.stderr)
        return 1
    ir_text = generate(module.module, triple=llvm.get_default_triple())
    out = args.output or (os.path.splitext(args.file)[0] + ".ll")
    with open(out, "w", encoding="utf-8") as f:
        f.write(ir_text)
    print(f"wrote {out}")
    return 0


def cmd_apk(args) -> int:
    from .mobile import ABIS, build_with_gradle, generate_project
    slug = os.path.splitext(os.path.basename(args.file))[0].replace("_", "-")
    out_dir = args.out or os.path.join("dist", slug + "-android")
    name = args.name or slug.replace("-", " ").title()
    package = args.package or f"com.psd.{slug.replace('-', '')}"
    abis = [a.strip() for a in args.abi.split(",") if a.strip()]
    bad = [a for a in abis if a not in ABIS]
    if bad:
        print(f"error: unknown ABI(s): {', '.join(bad)} (choose from {', '.join(ABIS)})",
              file=sys.stderr)
        return 1
    try:
        result = generate_project(args.file, out_dir, name, package, args.port, abis)
    except (LexError, ParseError) as e:
        print(f"{args.file}:{e}", file=sys.stderr)
        return 1
    except LoaderError as e:
        print(e, file=sys.stderr)
        return 1
    except CheckError as e:
        for line in e.errors:
            print(f"{args.file}: {line}", file=sys.stderr)
        return 1
    print(f"Android project generated: {out_dir}")
    for abi, msg in result["binaries"].items():
        print(f"  native binary [{abi}]: {msg}")
    built = [a for a, m in result["binaries"].items() if not m.startswith("SKIPPED")]
    if not built:
        print("  note: project has no PSD binary yet — install zig "
              "(`pip install ziglang`) or set ANDROID_NDK_HOME, then re-run.")
    if args.build:
        ok, msg = build_with_gradle(out_dir)
        if ok:
            print(f"APK built: {msg}")
        else:
            print(msg)
            return 1
    else:
        print("Next: open the project in Android Studio (Build > Build APK) "
              "or run `psd apk ... --build` with JDK+SDK+Gradle installed.")
def cmd_doc(args) -> int:
    import json as _json
    from . import catalog
    if args.name:
        hit = catalog.find(args.name)
        if hit is None:
            import difflib
            names = [b[0] for b in catalog.BUILTINS] + [
                f[0] for fns in catalog.STDLIB.values() for f in fns]
            close = difflib.get_close_matches(args.name, names, n=1)
            hint = f" — did you mean '{close[0]}'?" if close else ""
            if args.json_out:
                print(_json.dumps({"ok": False, "error": f"no such function '{args.name}'{hint}"}))
            else:
                print(f"no such function '{args.name}'{hint}", file=sys.stderr)
            return 2
        if args.json_out:
            print(_json.dumps({"ok": True, **hit}))
        else:
            if hit["kind"] == "builtin":
                print(f"{hit['signature']}  ->  {hit.get('returns', '')}")
            else:
                print(hit["signature"])
            print(f"  {hit['description']}  [{hit['kind']}"
                  + (f": {hit['module']}" if hit["kind"] == "stdlib" else "") + "]")
            if hit["kind"] == "stdlib":
                print(f"  use: {hit['import']}")
        return 0
    if args.json_out:
        print(_json.dumps(catalog.as_json()))
    else:
        data = catalog.as_json()
        print(f"PSD {catalog.VERSION} — {len(data['builtins'])} builtins, "
              f"{sum(len(v) for v in data['stdlib'].values())} stdlib functions")
        groups: dict = {}
        for b in data["builtins"]:
            groups.setdefault(b["group"], []).append(b)
        for g, bs in groups.items():
            print(f"\n[{g}]")
            for b in bs:
                print(f"  {b['signature']:<52} {b['description']}")
        for mod, fns in data["stdlib"].items():
            print(f"\n[{mod}]  (import \"{mod}\" as {mod[4:-4]})")
            for f in fns:
                print(f"  {f['signature']:<52} {f['description']}")
    return 0


def cmd_guide(args) -> int:
    from . import catalog
    if args.json_out:
        import json as _json
        print(_json.dumps({"rules": catalog.QUICK_RULES,
                            "skeleton": catalog.SKELETON,
                            "catalog": "psd doc --json"}))
    else:
        print(catalog.render_guide(), end="")
    return 0


def cmd_fmt(args) -> int:
    from .fmt import format_source
    bad = False
    for path in args.files:
        try:
            with open(path, encoding="utf-8") as f:
                src = f.read()
        except OSError as e:
            print(f"error: cannot read {path}: {e.strerror}", file=sys.stderr)
            return 1
        out = format_source(src)
        if args.check:
            if out != src:
                print(f"{path}: needs formatting")
                bad = True
        elif args.write:
            if out != src:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(out)
                print(f"formatted {path}")
        else:
            print(out, end="")
    return 1 if bad else 0


def cmd_mcp(args) -> int:
    from .mcp_server import serve
    print("psd mcp: serving on stdio (tools: guide, doc, example, "
          "template, check, fmt, run)", file=sys.stderr)
    return serve()


def cmd_bundle(args) -> int:
    from .bundle import build_bundle, estimate_tokens
    text = build_bundle(minimal=getattr(args, "minimal", False))
    out = args.output
    if out is None and not args.stdout:
        out = os.path.join("dist", "psd-ai-bundle.md")
    if out is not None:
        d = os.path.dirname(os.path.abspath(out))
        os.makedirs(d, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {out} ({len(text)} chars, ~{estimate_tokens(text)} tokens"
              f"{', minimal' if args.minimal else ''})")
        print("keep it private — upload it only to private AI projects, "
              "never to a public repo")
    if args.stdout:
        print(text, end="")
    return 0


def cmd_new(args) -> int:
    from . import templates
    name = os.path.basename(os.path.abspath(args.dir))
    main_path = os.path.join(args.dir, "main.psd")
    if os.path.exists(main_path):
        print(f"error: {main_path} already exists", file=sys.stderr)
        return 1
    os.makedirs(args.dir, exist_ok=True)
    with open(main_path, "w", encoding="utf-8") as f:
        f.write(templates.render(args.template, name))
    with open(os.path.join(args.dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(f"# {name}\n\nTemplate: {args.template} — "
                f"{templates.TEMPLATES[args.template][1]}\n\n"
                f"Run: `psd run main.psd`\n")
    print(f"created {args.dir}/ (template: {args.template})")
    print(f"next: psd check {main_path} && psd run {main_path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="psd", description="The PSD language toolchain")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("build", help="compile to a native binary")
    p.add_argument("file")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--no-cache", action="store_true", help="ignore the build cache")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("run", help="compile and run")
    p.add_argument("file")
    p.add_argument("prog_args", nargs="*", default=[])
    p.add_argument("--no-cache", action="store_true", help="ignore the build cache")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("check", help="type-check only")
    p.add_argument("file")
    p.add_argument("--json", action="store_true", dest="json_out",
                   help="emit structured JSON diagnostics (for AI agents/tools)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("emit-llvm", help="emit LLVM IR")
    p.add_argument("file")
    p.add_argument("-o", "--output", default=None)
    p.set_defaults(func=cmd_emit_llvm)

    p = sub.add_parser("apk", help="generate an Android app project (APK-ready) from a PSD web app")
    p.add_argument("file")
    p.add_argument("--out", default=None, help="output directory (default dist/<name>-android)")
    p.add_argument("--name", default=None, help="app display name")
    p.add_argument("--package", default=None, help="applicationId, e.g. com.example.notes")
    p.add_argument("--port", type=int, default=8090, help="localhost port the PSD app serves (default 8090)")
    p.add_argument("--abi", default="arm64-v8a,x86_64", help="comma list: arm64-v8a,x86_64")
    p.add_argument("--build", action="store_true", help="also run gradle assembleDebug (needs JDK+SDK+Gradle)")
    p.set_defaults(func=cmd_apk)
    p = sub.add_parser("fmt", help="canonical formatting (4-space indent)")
    p.add_argument("files", nargs="+")
    p.add_argument("-w", "--write", action="store_true", help="rewrite files in place")
    p.add_argument("--check", action="store_true", help="exit 1 if any file needs formatting")
    p.set_defaults(func=cmd_fmt)

    p = sub.add_parser("doc", help="look up builtins/stdlib (the AI-queryable catalog)")
    p.add_argument("name", nargs="?", default=None, help="function name, e.g. try_run")
    p.add_argument("--json", action="store_true", dest="json_out")
    p.set_defaults(func=cmd_doc)

    p = sub.add_parser("guide", help="print the one-screen quick reference for AI agents")
    p.add_argument("--json", action="store_true", dest="json_out")
    p.set_defaults(func=cmd_guide)

    p = sub.add_parser("new", help="scaffold a project from a template")
    p.add_argument("dir")
    p.add_argument("--template", default="cli",
                   choices=["cli", "api", "web", "train", "agent"])
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("cache", help="build-cache info/clear")
    p.add_argument("action", nargs="?", default="info", choices=["info", "clear"])
    p.set_defaults(func=cmd_cache)

    p = sub.add_parser("mcp", help="serve PSD to AI models via MCP (stdio, private)")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("bundle", help="build the private AI bundle (upload to ChatGPT/Claude Projects)")
    p.add_argument("-o", "--output", default=None,
                   help="output path (default: dist/psd-ai-bundle.md)")
    p.add_argument("--minimal", action="store_true",
                   help="guide + catalog + 3 examples only (small contexts)")
    p.add_argument("--stdout", action="store_true", help="also print the bundle")
    p.set_defaults(func=cmd_bundle)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
