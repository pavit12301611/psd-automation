# AI Guide: Writing PSD Code

You are writing code in **PSD** — a statically-typed, compiled language with
Python-like syntax. Follow this guide exactly and your code will compile
first try. If it doesn't, compiler errors name the line, column, and the fix.

## 0. The fast path (use the toolchain, don't memorize this file)

```text
psd guide                 # one-screen reference: 10 rules + skeleton + every signature
psd doc --json            # full machine-readable catalog (builtins + stdlib, verified in sync)
psd doc try_run           # one function: signature, description, import line
psd new app --template web   # compiling skeleton (cli|api|web|train|agent) — start from working code
psd check --json app.psd  # {"ok", "errors": [{file, line, col, message, hint, snippet}]}
psd fmt -w app.psd        # canonical layout — formatting is never your problem
psd run app.psd           # cached build: ~1s cold, ~0.09s warm (psd cache info/clear)
psd mcp                   # serve these tools to an AI client via MCP (repo stays private)
psd bundle                # one-file knowledge bundle for private ChatGPT/Claude Projects
```

> Giving a model PSD without publishing the repo? MCP (local agents) or
> the bundle (chat models) — full playbook: docs/PRIVATE_AI_ACCESS.md.

The loop that converges fastest: **scaffold or write → `check --json` →
apply each error's `hint` → `fmt -w` → `run`**. Common Python-isms are
diagnosed with their PSD spelling (`'single quotes'`, `None`, `list[int]`,
`input()` all produce fix-it messages), so keep the pattern that caused
the error out of your next attempt.

## 1. The 15 rules that matter

1. Statements end at a **newline** (`;` also works). Blocks use `{ }`.
2. Strings use double quotes. **Plain strings are literal** — `{x}` stays
   `{x}`. Interpolation requires an **`f` prefix**: `f"hi {name}"`.
3. Inside f-strings: `{expr}` interpolates, `{{` and `}}` are literal braces.
4. Types: `int` (64-bit), `float` (64-bit), `bool`, `str`, `json`,
   `[T]` (list), `{str: T}` (map). Inference works: `let x = 5` is `int`.
5. Parameters **require** types: `fn add(a: int, b: int) -> int`. The return
   type is optional (inferred from `return` statements).
6. `int` promotes to `float` automatically where floats are expected.
7. Conditions must be `bool` — `if 5` is an error. Use `x > 0`, `flag`, etc.
8. `and` / `or` / `not` (or `&&` / `||` / `!`) are the logic operators.
9. Iteration: `for i in range(a, b)`, `for x in myList`, `for k in obj.keys()`.
   `break` / `continue` work as expected.
10. Mixed-type literals become `json`: `{"a": 1, "b": "x"}` is `json`.
    Homogeneous literals keep static types: `{"a": 1, "b": 2}` is `{str: int}`.
    json + json/scalar `==`/`!=`, `if`/`while`, and str-style methods work
    directly; arithmetic still needs `int(...)`/`float(...)`.
11. `json` values need explicit conversion before arithmetic:
    `int(data["count"]) + 1`. Indexing `data["a"][0]["b"]` always returns `json`.
    But `==`/`!=`, `if`/`while` conditions and str-style methods work on `json`
    directly (Python-like truthiness).
12. Methods use dots: `s.trim()`, `s.split(",")`, `xs.push(v)`, `m.keys()`.
13. Empty literals need annotations: `let a: [int] = []`, `let m: {str: str} = {}`.
14. Functions must be defined **before use**, unless they declare a return type.
15. `fn main()` is the entry point (no params; returns void or int).

## 2. Cheat sheet

```psd
# modules (multi-file programs)
import "lib/greet.psd" as g          # g.hello("x") -- namespaced
from "lib/greet.psd" import hello    # hello("x")   -- direct
import "std/math.psd" as math        # bundled stdlib
```

```psd
# variables
let count = 0
let name = "ada"
let pi = 3.14
let ok = true
let xs = [1, 2, 3]
let cfg = {"host": "localhost", "port": 8080}     # {str: ...} -> json (mixed)
count += 1

# strings
let s = "  Hello  "
s.trim()                  # "Hello"
s.trim().upper()          # "HELLO"
s.contains("ell")         # true
s.replace("Hello", "Hi")  # "  Hi  "
"a,b,c".split(",")        # ["a", "b", "c"]
["a", "b"].join("-")      # "a-b"
"ab".repeat(3)            # "ababab"
"42".to_int()             # 42
f"{name} has {count}"     # interpolation (f-prefix!)

# lists
xs.push(4); xs.pop()
xs.len(); xs.contains(2); xs.reverse()
xs[0] = 99

# maps
let m: {str: int} = {"a": 1}
m["b"] = 2; m["a"] += 10
m.has("a"); m.get("zz", -1); m.remove("a")
m.keys(); m.values(); m.len()

# json
let data = json_parse("{\"users\": [{\"name\": \"ada\"}]}")
data["users"][0]["name"]          # json
data["users"][0]["name"].upper()  # "ADA" (str-style methods work on json strings)
str(data["users"][0]["name"])     # "ada"
int(data["count"]) + 1
data["users"][0]["name"] = "grace"
json_encode(data)                 # back to text
data["ok"] == true                # compare json with scalars directly
if data["ok"] { print("ok") }    # json is truthy like Python (bool value, != 0, non-empty)
for u in data["users"] { print(str(u["name"])) }

# files
let text = read_file("notes.txt")
write_file("out.txt", "hello\n")
append_file("out.txt", "more\n")
exists("out.txt"); delete("out.txt")
list_dir(".")

# processes (two APIs -- know the difference!)
let out = capture(["ls", "-1"])     # argv-based, NO shell -- safe with any input
                                    # returns STDOUT; stderr passes through to the
                                    # console; panics if the command exits non-zero
let code = run(["make", "build"])   # exit code as int
let up = capture_shell("ls | wc -l")  # EXPLICIT shell: pipes work, injection risk
                                      # never pass untrusted strings to shell()

# recoverable errors (automations: failures are data, not crashes)
let r = try_read_file("config.json")
if r["ok"] {
    print(str(r["value"]))
} else {
    print(str(r["error"]))
}
let out2 = try_capture(["ls", "/tmp"], 5)     # {ok, value|error, code}
let out3 = try_capture_shell("make", 60)      # optional timeout seconds
let out4 = try_int("42")                      # {"ok": true, "value": 42}
let out5 = try_json_parse("[1, 2]")           # {"ok": true, "value": [1, 2]}
let out6 = try_http_get("http://127.0.0.1:9/")  # {"ok": false, "error": ...}

# web serving (built-in HTTP server; 127.0.0.1, sequential)
let port = http_listen(8080)
let req = http_accept()                       # {method, path, query, body, headers}
http_reply(200, "text/html", "<h1>hi</h1>")
http_redirect("/")                            # 302 (after a form POST)
# GUI stdlib: import "std/web.psd" as web -> web.page/h1/table/list_items
#   (escape text by default), web.parse_form(body), web.reply_json(200, v)

# ML math + time/random + automation extras
exp(x); log(x); pow(x, y); sqrt(x)            # float math (sigmoid = 1/(1+exp(-x)))
sleep_ms(250); time_ms()                      # backoff/polling, epoch ms
random(); rand_int(10)                        # [0, 1) float / 0..9 int
read_line("name? ")                           # stdin line ("" at EOF)
cwd(); make_dir("out")                        # make_dir is idempotent
try_run(["make"], 60)                         # {"ok", "code"} recoverable
try_float("2.5"); try_make_dir("out")         # json envelopes, like other try_*
let safe = html_escape("<b>")                 # &lt;b&gt;
let enc = url_encode("a b")                   # a%20b
let dec = url_decode("a+b%20c")               # a b c

# http (requires curl)
let body = http_get("https://api.example.com/items")
let resp = http_post(url, json_encode(payload), "application/json")
let headers = {"Authorization": "Bearer TOKEN"}
let r = http_request("POST", url, json_encode(payload), headers)
int(r["status"]); r["ok"]; str(r["body"])

# process
args()                    # [str] command line args
env("HOME"); env("X", "default")
exit(1)

# conversions
str(x); int(x); float(x)    # work on int/float/bool/str/json
```

## 3. Common recipes

**Read a config file and act on it**
```psd
fn main() {
    let cfg = json_parse(read_file("config.json"))
    let host = str(cfg["host"])
    let port = int(cfg["port"])
    print(f"connecting to {host}:{port}")
}
```

**Call an LLM-style API**
```psd
fn main() {
    let payload = {"model": "gpt", "messages": [{"role": "user", "content": "hi"}]}
    let headers = {"Authorization": "Bearer " + env("API_KEY")}
    let r = http_request("POST", "https://api.example.com/v1/chat", json_encode(payload), headers)
    if r["ok"] {
        let data = json_parse(str(r["body"]))
        print(str(data["choices"][0]["message"]["content"]))
    } else {
        print("failed with status", int(r["status"]))
    }
}
```

**Batch-process files**
```psd
fn main() {
    for f in list_dir("logs") {
        if f.ends_with(".txt") {
            let text = read_file("logs/" + f)
            print(f, text.trim().split("\n").len(), "lines")
        }
    }
}
```

**CLI tool**
```psd
fn main() {
    let a = args()
    if a.len() != 2 {
        print("usage: tool <input> <output>")
        exit(2)
    }
    write_file(a[1], read_file(a[0]).upper())
}
```

**Serve a small web interface** (full working program; see
`examples/webapp.psd` for a complete app):

```psd
fn main() {
    let port = http_listen(8080)
    print("serving on " + str(port))
    while true {
        let req = http_accept()
        if req["path"] == "/" {
            http_reply(200, "text/html", "<h1>hello</h1>")
        } else {
            http_reply(404, "text/plain", "not found")
        }
    }
}
```

**Fit a tiny model and save its parameters** (full working program; see
`examples/ml_tiny.psd`):

```psd
import "std/ml.psd" as ml

fn main() {
    let xs = [1.0, 2.0, 3.0]
    let ys = [3.0, 5.0, 7.0]
    let m = ml.fit(xs, ys)
    if m["ok"] {
        print(str(ml.predict(m, 4.0)))
        ml.save("/tmp/model.json", m)
    }
}
```

**Orchestrate a real ML runtime** (the interop pattern): write a json
dataset, `try_capture(["python3", "trainer.py", ...], secs)` to run the trainer argv-based (no shell) with a timeout, validate the returned model, then read it back with `ml.load` — `examples/ml_interop.psd` +
`examples/lib/train_ols.py` demonstrate it. PSD itself has no tensors, no GPU, and no autodiff; `std/ml` is scalar least squares on `[float]`.

**Train a classifier from CSV** (see `examples/train_classifier.psd`):
`std/data.psd` loads numeric CSV (`load_numbers` → `to_matrix` /
`column` / `features`); `std/ml.psd` adds `fit_multi` (multivariate
regression), `fit_logistic` + `predict_label` + `accuracy`, `r2`,
`fit_scaler`/`apply_scaler`, `train_test_split`.

**Give a model tools** (see `examples/agent_tools.psd`):
`import "std/agent.psd" as agent` — `agent.tool_spec(name, desc, params)`,
`agent.chat_payload(model, msgs, tools)`, `agent.chat(url, key, payload)`
(OpenAI-compatible), then `agent.tool_calls(resp)` →
`[{id, name, args}]`; run tools in an explicit if/elif dispatch and
answer with `agent.tool_result(id, text)`. Fine-tuning datasets:
`data.chat_example(...)` + `data.save_jsonl(path, rows)`.
Machine-check your work with `psd check --json file.psd`
(`{"ok", "errors": [{file, line, col, message}]}`).

**Build a GUI app** (see `examples/gui_dashboard.psd`): handlers return
`web.page(title, web.h1(...) + web.list_items(...) + ...)`; forms POST,
then `web.redirect_to("/")`; JSON endpoints use `web.reply_json`.
Read cookies/auth via `req["headers"]` (lowercased names).

## 4. Error messages are fixable — read them

```
prog.psd: error: condition must be bool, got int (line 3, col 8)
prog.psd: error: cannot assign str to int (line 5, col 5)
prog.psd: error: unknown function 'prnt' — did you mean 'print'? (line 1, col 1)
```

Typical causes: missing `f` prefix on an interpolated string; `json` used in
arithmetic without `int(...)`; plain `str` where `json` expected (build a
mixed literal or annotate `let x: json = ...`); condition is not a bool.

## Agent tasks

Each task below has a clear expected behavior and an executable test in
`tests/`. The test is the spec: changes to PSD must keep it passing, and
new features should arrive with a task-style test of the same shape.

| # | Task | Expected behavior | Executable test |
|---|------|-------------------|-----------------|
| 1 | **Automation**: read a file, run a command, write a report; every failure is data, never a crash | missing file → `{\"ok\": false, ...}`; failing command → non-zero `code`; `sleep` killed by timeout → `code == -1` | `tests/cases/try_ops.psd` |
| 2 | **ML interop**: write json data, run an external trainer with a timeout, validate and load the model, predict | `w ≈ 2.0`, `predict(6) ≈ 13.0`; trainer failure/timeout exits nonzero with a message; missing/bad model → useful error envelope | `tests/cases/ml_interop.psd`, `tests/cases/ml_timeout.psd`, `tests/cases/ml_trainer_fail.psd`, `tests/cases/ml_load_bad.psd` |
| 3 | **App change**: extend the notes web app while keeping its contract | new/existing routes answer over HTTP; XSS payloads are escaped, never raw; `/nope` → 404; `/api/status` returns JSON | `tests/run_tests.py` (`check_server_integration`) |

To solve a task as an agent: read the test file first, make it run, then
change the smallest amount of code (compiler, runtime, example, or test)
needed, and re-run the suite.

## 5. What PSD does NOT have (do not invent)

- No classes, no exceptions/try-catch (runtime errors exit with
  `psd: runtime error: ...`; use `try_*` builtins to recover from ordinary
  failures instead)
- No import cycles (compile error), no re-export namespacing tricks —
  just `import ... as` and `from ... import`.
- Server concurrency exists only as `http_listen_pool` (preforked
  worker processes, per-worker state). No TLS, no WebSockets — put a
  reverse proxy in front for HTTPS.
- No tensors/GPU/autograd. `std/ml` = small tabular models (scalar and
  multivariate regression + logistic classification);
  `std/nn` + `std/numeric` = small MLP backprop training on matrices;
  delegate deep learning to an external runtime via `run`.
- Images: PPM/BMP only via `image_info`/`image_process` — no JPEG/PNG,
  no video. Terminal games via `std/game.psd`; no graphics engine.
- No string indexing (`s[0]` is invalid — use `.split()` / `.contains()`).
- No arithmetic on `json` without `int(...)` / `float(...)`.
- No truthiness for typed values: `if 5` is a compile error
  (`json` values ARE truthy, Python-style).
- No shell in `run`/`capture` — use `shell()`/`capture_shell()` explicitly.

## 6. Security: the one rule that matters

`run(["prog", arg...])` and `capture([...])` are **injection-safe** (no shell).
`shell("...")` and `capture_shell("...")` hand the string to the system shell —
only use them with fully trusted strings. When embedding PSD in a larger app,
set `PSD_DISABLE_PROCESS=1`, `PSD_DISABLE_HTTP=1`, or `PSD_DISABLE_FILES=1`
to turn capability groups off at runtime.

⚠️ The capability switches are **not a sandbox**: they disable specific
builtins, not the process. With `PSD_DISABLE_FILES=1`, `shell("cat file")`
still reads files. For untrusted programs use an OS-level sandbox
(container/VM) **and** disable all relevant switches.

## 7. Modules: designing clean interfaces

Multi-file programs are how you design software in PSD. Two import forms:

```psd
import "lib/greet.psd" as g            # g.hello("x") -- namespaced calls
from "lib/greet.psd" import hello      # hello("x")   -- direct calls
import "std/math.psd" as math          # bundled stdlib (math.min, math.clamp, ...)
```

Rules: paths are relative to the importing file and end in `.psd`; function
names are file-scoped (two modules can both define `helper`); an alias may
not collide with a function name in the file; import cycles are a compile
error; diamond imports load once. `fn main()` must be in the entry file.

### Structuring an app (the pattern that works)

```
app/
  main.psd        # entry: wiring + top-level flow only
  ui.psd          # rendering / interface functions
  model.psd       # data shaping + validation
  io.psd          # files, HTTP, processes
```

Guidance for AI agents building apps:

1. **A module's exported functions ARE its interface.** Design their
   signatures first (names, params, return types), write stubs, then fill in.
   Callers only use the exports — helpers stay internal-by-convention.
2. **Keep `main.psd` thin**: orchestration and flow; put real work in
   modules. This makes programs easy to review and change.
3. **Group by responsibility, not by size** — `io.psd` owns all side effects
   (files/HTTP/process), `model.psd` owns pure transformations. Pure modules
   are trivially testable.
4. **Annotate return types of exported functions** (`fn parse(x: str) -> json`)
   — callers get inference even before the module is checked.
5. **Use `json` as the interchange type** between modules when structures
   evolve (maps for fixed shapes, json for nested/heterogeneous data).
6. **Error policy**: at boundaries pre-check (`exists`, `has`, `len(x) > 0`)
   or return json result envelopes like `{"ok": true, "value": ...}` /
   `{"ok": false, "error": "..."}` so automation failures are data,
   not crashes.

For GUIs/web UIs, use `std/web.psd` components (escaping by default) and
keep rendering isolated in one module; the browser is PSD's GUI runtime —
there is no desktop widget toolkit, no WebSockets, no sessions (read
cookies from `req["headers"]` and manage state yourself).
