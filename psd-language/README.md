# psd-language

**PSD** is a fast, simple, cross-platform programming language — Python-like
syntax, Rust-like performance, native binaries, built to be easy for both
humans **and AI agents** to write.

```psd
fn greet(name: str) {
    print(f"Hello, {name}!")
}

fn main() {
    let nums = [1, 2, 3, 4, 5]
    for n in nums {
        print(f"n = {n}")
    }
    greet("world")
}
```

- **Fast** — ahead-of-time compiled through **LLVM** (same optimizer Rust uses).
  Benchmarked **~23–34x faster than Python 3.11** on the standard benchmarks
  (see below; numbers vary by machine).
- **Simple** — tiny grammar designed so both humans *and AI models* can write
  it reliably: explicit braces, newline-terminated statements, one obvious way
  to do things, clear compile errors with line numbers.
- **Batteries for apps & automations** — strings with methods, lists, maps,
  dynamic `json`, file I/O, shell exec, HTTP, env/args. See **AI_GUIDE.md**
  for the compact reference made for LLMs.
- **Typed** — static types with local inference (`let x = 5` just works).
  Type errors are caught at compile time, not at 3 a.m. in production.
- **Cross-platform** — emits LLVM IR + portable C runtime; builds native
  binaries for Linux / macOS (see Platform support below).
- **Safe memory** — automatic reference counting (like Swift). No garbage
  collector pauses, no manual `free()`. Verified leak-free with AddressSanitizer.

---

## Quick start

Requirements: Python 3.10+, a C compiler (`cc`/`gcc`/`clang`), and `llvmlite`.

```bash
pip install llvmlite            # or: python3 -m venv .venv && .venv/bin/pip install llvmlite

python3 -m psd run hello.psd    # compile + run
python3 -m psd build hello.psd  # compile to a native binary
python3 -m psd check hello.psd  # type-check only
python3 -m psd emit-llvm hello.psd   # inspect the generated LLVM IR
python3 -m psd apk app.psd           # generate an Android APK project (psd/mobile.py)
```

(With the venv activated you can use `psd` from `bin/` as a shortcut.)

### The AI fast loop

PSD is built so an AI agent (or a human in a hurry) iterates in seconds,
with the toolchain answering its own questions:

| Command | What it gives you |
|---|---|
| `psd guide` | one-screen reference: 10 rules, skeleton, every signature |
| `psd doc --json` / `psd doc <name>` | machine-readable catalog of all 54 builtins + stdlib (test-verified in sync) |
| `psd new app --template web` | compiling project skeleton (`cli api web train agent`) |
| `psd check --json app.psd` | `{file, line, col, message, hint, snippet}` per error |
| `psd fmt -w app.psd` | canonical formatting — layout is never a failure mode |
| `psd run app.psd` | **content-hash build cache**: ~1.0 s cold → **~0.09 s** warm (`psd cache info/clear`, `--no-cache` to bypass) |

Measured on this repo (hello.psd): cold run 0.99 s, warm runs 0.089 s —
an ~11× faster generate→check→run→fix loop, with hash keys over every
source file + the toolchain, so edited code can never serve a stale
binary. See AI_GUIDE.md §0 for the workflow.

### Give AI models PSD — without making this repo public

Keep the repo **private**; give models tools or knowledge, not git access:

- **Coding agents** (Claude Desktop/Code, Cursor, VS Code): `psd mcp`
  serves PSD over MCP from your private clone — `psd_guide`, `psd_doc`,
  `psd_example`, `psd_template`, `psd_check`, `psd_fmt`, and a sandboxed
  `psd_run`. Ready-made configs in `integrations/`.
- **Chat models** (ChatGPT, Claude.ai, Gemini): `psd bundle` builds one
  private file (`dist/psd-ai-bundle.md`, ~16k tokens; `--minimal` ~6k)
  from the live catalog + guide + SPEC + examples — upload it to a
  private Project/Gem with `integrations/SYSTEM_PROMPT.md`. `dist/` is
  gitignored: never commit or publish the bundle.
- **Trusted agents/CI**: scoped, read-only, short-lived private-repo
  access (OAuth app grants, fine-grained PAT, deploy keys) — never a
  token pasted into a chat.

Full playbook with per-client setup and a security checklist:
**docs/PRIVATE_AI_ACCESS.md**.

## Benchmarks (vs Python 3.11, same machine, same algorithms)

| Benchmark | PSD | Python | Speedup | Kind |
|---|---|---|---|---|
| `fib(32)` recursive calls | **0.008 s** | 0.24 s | **~31x** | measured |
| Mandelbrot 200×200 (float) | **0.003 s** | 0.07 s | **~23x** | measured |
| Integer loop, 10⁹ iterations | **3.05 s** | ~104 s | **~34x** | Python = 1e8 sample × 10 (extrapolated) |

The loop benchmark runs PSD for the full 10⁹ iterations; Python is timed on a
1e8-iteration sample and both its time and its iteration count are scaled ×10
for comparison, so outputs agree (see `bench/run_bench.py`). Recorded on Linux
x86-64, Python 3.11.2, llvmlite (LLVM 22) O3 pipeline, gcc 12.2 link. Numbers
vary by machine — reproduce them yourself:

```bash
python3 bench/run_bench.py          # full run, prints env + table
python3 bench/run_bench.py --quick  # fast smoke version (used in CI)
```
Output mismatches fail the benchmark runner with a nonzero exit status.

---

## Language tour

### Variables and types

```psd
let count = 10            # int  (64-bit)
let pi = 3.14159          # float (64-bit)
let name = "PSD"          # str  (immutable, refcounted)
let ok = true             # bool
let xs = [1, 2, 3]        # [int]
let grid: [[int]] = [[1, 2], [3]]   # nested lists
let empty: [float] = []   # empty lists need an annotation
```

Types are inferred. `int` promotes to `float` automatically where floats meet
ints (`3 + 4.5 == 7.5`). Reassignment and compound assignment work anywhere:

```psd
let n = 0
n = n + 1
n += 2
```

### Strings

```psd
let who = "world"
print(f"hello {who}")          # f-strings interpolate
print(f"math: {2 + 2}")        # any expression inside { }
print("a" + " " + "b")         # concatenation
print(len(who))                # 5
who.upper()                    # "WORLD"
who.contains("or")             # true
"a,b,c".split(",")             # ["a", "b", "c"]
["a", "b"].join("-")           # "a-b"
```

Plain strings are literal (`"{x}"` prints `{x}`) — so JSON inside strings
just works. f-strings interpolate with `{expr}`; `{{`/`}}` escape braces.
Escapes: `\n` `\t` `\r` `\0` `\\` `\"`.

### Maps and JSON

```psd
let cfg = {"host": "localhost", "port": 8080}   # mixed literal -> json
print(cfg["host"], int(cfg["port"]) + 1)

let scores: {str: int} = {"ada": 10}            # homogeneous -> static map
scores["grace"] = 12
scores["ada"] += 5
print(scores.get("zz", -1), scores.has("ada"))

let data = json_parse(read_file("data.json"))
print(str(data["users"][0]["name"]))
for u in data["users"] {
    print(str(u["name"]))
}
```

### Files, shell, HTTP (automation builtins)

```psd
let text = read_file("notes.txt")
write_file("out.txt", text.upper())
append_file("out.txt", "done\n")
let listing = capture(["ls", "-1"])    # stdout as str (stderr passes through)
let code = run(["make", "build"])      # argv, no shell; returns exit code
let body = http_get("https://example.com/api")
let r = http_request("POST", url, json_encode(payload),
                     {"Authorization": "Bearer " + env("API_KEY")})
print(int(r["status"]), str(r["body"]))
```

### Control flow

```psd
if score > 90 {
    print("A")
} elif score > 80 {
    print("B")
} else {
    print("C")
}

while n < 10 {
    n += 1
    if n % 2 == 0 {
        continue
    }
    if n > 7 {
        break
    }
    print(n)
}

for i in range(0, 3) {            # 0..2
    print(i)
}
let myList = ["a", "b"]
for item in myList {              # iterate a list
    print(item)
}
```

### Functions

```psd
fn add(a: int, b: int) -> int {   # parameter types are required
    return a + b                  # return type optional (inferred)
}

fn shout(msg: str) {              # no return type -> void
    print(msg + "!")
}
```

Functions must be defined before use *unless* they declare an explicit return
type (this keeps type inference simple and predictable).

### Lists

```psd
let xs = [10, 20, 30]
print(xs[0], len(xs))
xs[1] = 99
xs[2] += 5
push(xs, 40)          # append
let last = pop(xs)    # remove + return last element
```

Lists are dynamic, homogeneous and bounds-checked (bad index = clean runtime
error, not a segfault).

### Builtins

| Builtin | Meaning |
|---|---|
| `print(a, b, ...)` | print values separated by spaces, then newline |
| `len(x)` | length of a `str`, list, map or json |
| `push(list, v)` / `pop(list)` | append / remove-last |
| `str(x)` / `int(x)` / `float(x)` | conversions (work on json too) |
| `range(a, b)` / `range(n)` | integer ranges for `for` loops |
| `read_file` `write_file` `append_file` `exists` `delete` `list_dir` | files |
| `run([prog, ...])` `capture([prog, ...])` | run processes — **no shell, injection-safe** |
| `shell(cmd)` `capture_shell(cmd)` | explicit shell execution (trusted strings only) |
| `http_get` `http_post` `http_request` | HTTP client (needs `curl`) |
| `http_listen` `http_accept` `http_reply` | built-in HTTP **server** (web interfaces) |
| `try_read_file` `try_write_file` `try_int` `try_json_parse` | recoverable errors — return `{"ok":..., "value"/"error":...}` |
| `try_http_get` `try_capture` `try_capture_shell` | recoverable HTTP/process (+ optional timeout seconds) |
| `html_escape` `url_encode` `url_decode` `sqrt` | safe templating, form parsing, math |
| `exp(x)` `log(x)` `pow(x, y)` | float math for ML (sigmoid, losses) |
| `sleep_ms(ms)` `time_ms()` `random()` `rand_int(n)` | timing, backoff, sampling |
| `read_line([prompt])` `cwd()` `make_dir(path)` | interactive CLIs, dirs |
| `try_run(argv[, secs])` `try_make_dir` `try_float` | recoverable process/dir/parse |
| `http_redirect(url)` | 302 redirect (form flows) |
| `json_parse(s)` `json_encode(v)` | JSON round-trip |
| `args()` `env(name[, default])` `exit(code)` | process integration |

Capability switches for restricted contexts: `PSD_DISABLE_PROCESS=1`,
`PSD_DISABLE_HTTP=1`, `PSD_DISABLE_FILES=1` (see SPEC.md §7). Note: these
are runtime capability switches, **not a sandbox** — untrusted programs need
an OS-level sandbox plus disabling all relevant switches.

### Recoverable errors (`try_*`) — reliable automations

Ordinary failures don't have to kill the program. Every `try_*` builtin
returns a json envelope — `{"ok": true, "value": ...}` or
`{"ok": false, "error": ...}`:

```psd
fn main() {
    let r = try_read_file("config.json")
    if r["ok"] {
        print(str(r["value"]))
    } else {
        print("fallback: " + str(r["error"]))
    }
    let out = try_capture(["curl", "--version"], 5)   # 5s timeout
    let code = try_capture_shell("make build", 60)    # {ok, code, value}
}
```

`try_capture` / `try_capture_shell` add `"code"` (exit code, `-1` on
timeout) and `"value"` (captured stdout) to error envelopes.

### Web interface (built-in HTTP server)

```psd
fn main() {
    let port = http_listen(8080)     # binds 127.0.0.1; prints nothing
    while true {
        let req = http_accept()      # {method, path, query, body, headers}
        http_reply(200, "text/html", "<h1>hi " +
            html_escape(str(req["query"])) + "</h1>")
    }
}
```

`python3 -m psd run examples/webapp.psd` starts a complete notes app
(routing, forms, escaping, json endpoint). Build pages with
`import "std/web.psd" as web` — escaping components (`web.page`,
`web.h1`, `web.table`, `web.list_items`), `web.parse_form`,
`web.reply_json`, `web.redirect_to` — see `examples/gui_dashboard.psd`.
Requests carry `req["headers"]` (lowercased names); `http_redirect(url)`
answers 302. For concurrent traffic, `examples/server_pool.psd` uses
`http_listen_pool(port, host, workers)` (preforked workers, streamed
files, redirects, request headers). Limits: HTTP/1.1 only, no TLS —
put a reverse proxy in front for real deployments.

### ML (models as json)

`import "std/ml.psd" as ml` fits `y = w*x + b` (least squares).
`import "std/nn.psd" as nn` trains small MLPs with real backpropagation
on `std/numeric.psd` matrices (`examples/nn_xor.psd` learns XOR and
persists the model as json; `bench/nn_bench.psd` vs
`bench/nn_compare.py` measures ~2× vs pure Python). PSD has no
tensor/GPU support; std/nn is for small CPU-side models.
### Games, apps, images

Terminal games: `std/game.psd` + `read_key()`/`term_*`
(`examples/game_snake.psd`). Local "apps": serve a responsive PWA UI
with `std/web.psd` and `open_url()` (`examples/mobile_app.psd`).
Real Android apps: `psd apk examples/mobile_app.psd --name "PSD Notes"
--package com.psd.notes` generates a full Android Studio project with
your PSD program cross-compiled to a native on-device binary (ARM64 +
x86_64, via zig — no NDK needed); build the APK in Android Studio or
with `--build` when JDK/SDK/Gradle are installed. Images: native
PPM/BMP filters via `image_process` (`examples/image_tool.psd`).
Full honest capability matrix: `docs/CAPABILITIES.md`.

### ML interop and tabular models

`import "std/ml.psd" as ml` fits `y = w*x + b` (least squares),
multivariate regression (`fit_multi`), and binary logistic classifiers
(`fit_logistic` / `predict_label` / `accuracy`), with metrics
(`loss`, `rmse`, `r2`), feature scaling, and save/load of parameters as
json — see `examples/ml_tiny.psd` and `examples/train_classifier.psd`
(CSV → train → evaluate → save, via `import "std/data.psd" as data`).
To plug in a real ML runtime (PyTorch, scikit-learn, R), have PSD
orchestrate it over json files with `run([...])` — see
`examples/ml_interop.psd`. PSD has no tensor library and no GPU support;
std/ml is for small CPU-side models.

### Giving a model tools (agents)

`import "std/agent.psd" as agent` is the tool-calling loop as stdlib:
`tool_spec` schemas, `chat_payload` for OpenAI-compatible endpoints,
`tool_calls(resp)` parsing, and `tool_result(...)` replies — dispatch is
an explicit name → if/elif chain in your code, so the tool surface is
auditable. Fine-tuning datasets are `std/data.psd`'s `chat_example` /
`save_jsonl`. See `examples/agent_tools.psd` (runs offline) and the
honest scored audit in **docs/RATING.md**. For agent tooling,
`python3 -m psd check --json file.psd` emits structured diagnostics
(`{"ok": ..., "errors": [{file, line, col, message}]}`).

Methods (dot syntax): `str`: `upper lower trim contains starts_with ends_with
replace split repeat len to_int to_float` · `list`: `push pop contains reverse
len join` · `map`: `keys values has remove get len` · `json`: `keys has len push`.

### Modules (multi-file programs)

```psd
import "lib/greet.psd" as g            # namespace: g.hello(...)
from "lib/greet.psd" import hello      # direct: hello(...)
```

Paths are relative to the importing file (or `std/...` for the bundled
standard library, e.g. `import "std/math.psd" as math`). Each file is a
module: its functions are its interface, names are file-scoped, import
cycles are a compile error. See SPEC.md §3 for the full rules.

### Comments

```psd
# full-line comment
// also accepted
```

---

## How it works (compiler pipeline)

```
.psd source
   │  lexer          tokens (psd/lexer.py)
   │  parser         AST      (psd/parser.py)
   │  checker        types + inference (psd/checker.py)
   │  codegen        LLVM IR  (psd/codegen_llvm.py)
   │  llvmlite       real LLVM optimizer (O3, mem2reg, instcombine, vectorize)
   │  cc             link with the C runtime (psd/runtime.c)
   ▼
native binary
```

The generated code is genuine LLVM IR — you can inspect it with
`psd emit-llvm` and compile it with `clang file.ll runtime.c -O3` on any
platform. Memory is managed by automatic reference counting; every
`str`/`[T]` value is an owned reference and the compiler emits the increfs /
decrefs. The test suite is verified **leak-free under AddressSanitizer +
LeakSanitizer** (`PSD_SANITIZE=1 python3 tests/run_tests.py`).

## Project layout

```
psd/            the compiler package
  lexer.py      tokenizer (f-strings, plain strings)
  parser.py     recursive-descent parser
  checker.py    static typing + inference + clear errors
  codegen_llvm.py   LLVM IR emitter (ownership/RC aware)
  runtime.c     portable C11 runtime (str/list/map/json/shell/http/...)
  cli.py        psd build / run / check / emit-llvm
examples/       tour programs (hello, fibonacci, fizzbuzz, features, ...)
tests/          19 success cases + 16 compile-error cases + 15 runtime-error
                cases (50 programs), docs-snippet validation, HTTP integration
bench/          benchmark sources (psd + python)
SPEC.md         full language specification
AI_GUIDE.md     compact reference for LLMs writing PSD code
```

## Platform support

| Platform | Status |
|---|---|
| **Linux x86-64** | ✅ tested (test suite + ASAN/LSAN in CI and locally) |
| **macOS** | ✅ expected to work; covered by CI (test suite + sanitizer) |
| **Windows** | ❌ not yet — the runtime uses POSIX (`fork`, `execvp`, `popen`, `dirent`) |
| **Other CPU architectures** | works wherever LLVM + a C compiler exist (any `target triple`), untested |

The core compiler is portable by construction (LLVM IR + C11 runtime); the
*automation layer* (process/shell/file APIs) is what is POSIX-bound today.
`psd emit-llvm` output can be compiled with clang on any platform, but
`psd/runtime.c` currently needs POSIX APIs. HTTP additionally requires
`curl` on `PATH`. Platform-specific runtime implementations (starting with
Windows) are on the roadmap below.

## Roadmap (deliberate order)

1. **Semantics + test-suite stabilization** (done): edge-case definitions,
   runtime-error contract, CI on Linux + macOS.
2. **Modules / imports** (done): multi-file programs with file-scoped
   namespaces, `import ... as` / `from ... import`, bundled `std/math`.
3. **Windows runtime** (process/file layers) so the portability claim is
   tested, not aspirational.
4. **Standard library growth** (string extra, time, path) on the builtin layer.
5. **Structured error handling** (result-style, no exceptions) so automations
   can recover instead of exiting.
6. **Richer data types** (structs/records), then self-hosting exploration.

## Status

v0.3 — core language + automation layer: functions, int/float/str/bool, lists,
maps, dynamic `json`, string/list/map/json methods, files, argv-based shell-free
process execution (`run`/`capture`) plus explicit `shell`/`capture_shell`, HTTP
client via `curl`, recoverable `try_*` builtins (incl. timeouts), env/args/exit,
f-strings, modules/imports with bundled `std/math` and `std/ml`, a sequential
built-in HTTP server (localhost-only, no TLS), rc memory management
(ASan/LSan-verified). See SPEC.md for exact semantics and AI_GUIDE.md for the
LLM-oriented reference.

Honest scope notes: `std/ml` is scalar least-squares on `[float]` — not a
neural-network, tensor, GPU, or autodiff framework; delegate real training to
an external runtime over json files (see `examples/ml_interop.psd`). The
built-in HTTP server is sequential, HTTP/1.1, localhost by default, and is not
production-facing; put a reverse proxy in front. The `PSD_DISABLE_*` switches
disable builtins — they are not an OS-level sandbox.

Planned: structs/records, a standard library, Windows runtime, package manager,
self-hosting exploration.

## License

MIT
