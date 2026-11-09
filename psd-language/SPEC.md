# PSD Language Specification (v0.1)

PSD is a statically-typed, ahead-of-time compiled language with Python-like
syntax. It is designed to be easy to write reliably — by humans **and** by AI
models: a small unambiguous grammar, explicit `{ }` blocks, newline-terminated
statements, and one obvious way to do each thing.

## 1. Lexical structure

- Source files are UTF-8; extension `.psd`. Canonical layout (4-space
  block indent) is produced by `psd fmt` and never affects semantics.
- **Comments**: `# ...` or `// ...` to end of line.
- **Statements** end at a newline. `;` also ends a statement. Newlines inside
  `( )` and `[ ]` are ignored (line continuation). `{ }` delimit blocks and
  do not suppress newlines.
- **Identifiers**: `[A-Za-z_][A-Za-z0-9_]*`.
- **Keywords**: `fn let if elif else while for in return break continue
  true false and or not`. (`&&` and `||` are accepted aliases of `and`/`or`;
  `!` is an alias of `not`.)
- **Integer literals**: `123`, `1_000_000` (underscores allowed).
- **Float literals**: `3.14`, `1e-3`, `2.5e10`.
- **String literals**: double quotes. Plain strings are fully literal
  (braces are NOT special — JSON text inside strings just works). Escapes:
  `\n \t \r \0 \\ \"`. **f-strings** (`f"..."`) support `{expr}`
  interpolation; use `{{` / `}}` for literal braces inside f-strings.

## 2. Types

| Type | Description |
|---|---|
| `int` | 64-bit signed integer |
| `float` | 64-bit IEEE-754 float |
| `bool` | `true` / `false` |
| `str` | immutable UTF-8 string, reference-counted |
| `[T]` | dynamic list of `T` (e.g. `[int]`, `[[str]]`) |
| `{str: T}` | hash map with `str` keys and homogeneous `T` values |
| `json` | dynamic JSON value: null / bool / int / float / str / array / object |
| `void` | no value (functions that don't return) |

There are **no implicit conversions** except `int -> float` wherever a float
is expected (arithmetic with a float operand, assignment to a float-typed
slot, float parameters/returns, float list elements) and **any value -> json**
where a json is expected (the value is wrapped).

**Literal typing rule:** a list/map literal whose elements all share one type
gets the static type (`[1, 2]` is `[int]`, `{"a": 1}` is `{str: int}`); a
literal with mixed element types becomes `json` (`{"a": 1, "b": "x"}`).
Mixed literals therefore support full JSON nesting. Indexing a `json`
(`j["k"]`, `j[0]`) returns `json`.

**`json` in expressions — the definitive rules** (also used by AI_GUIDE.md):

| Operation | Allowed? | Notes |
|---|---|---|
| `j["k"]`, `j[0]` | yes | returns `json` |
| `j["k"] = v` | yes | `v` is wrapped to `json` |
| `j == v`, `j != v` | yes | scalar is wrapped; numbers compare across int/float |
| `if j` / `while j` | yes | Python-like truthiness (see Conditionals) |
| `j.trim()`, `j.upper()`, ... | yes | str-style methods (error if not a string) |
| `j + 1`, `j * 2`, `j < 5` | **no** | convert first: `int(j) + 1` |
| `j and true` | **no** | convert first: `int(j) != 0 and true` |

Conversions: `int(x)` / `float(x)` / `str(x)` accept `json` (with runtime
checks) and every scalar type.

## 3. Program structure

A program is a sequence of function definitions and must define:

```text
fn main() { ... }        # no parameters; returns void or int (exit code)
```

```text
fn name(param: Type, ...) -> ReturnType {   # return type optional
    ...
}
```

- Parameter types are **required** (keeps calling conventions explicit).
- The return type may be omitted; it is inferred from `return` statements
  (all must agree). Functions with no value-returning `return` are `void`.
- A value-returning function must return on **all paths**.
- Functions are visible to callers that appear **later** in the file, or
  earlier — but calling a function *before* it is defined requires an explicit
  return type (or a `void` body, which is pre-detected). Define before use,
  or annotate; this keeps inference simple and predictable.

### Modules and imports (multi-file programs)

A program is usually several `.psd` files. The entry file is the one passed
on the command line; it must define `fn main()`. Two import forms, both at
the top level of a file:

```
import "lib/greet.psd"          # module alias 'greet' (the file stem)
import "lib/greet.psd" as g     # explicit alias
from "lib/greet.psd" import hello, wave   # bind function names directly
```

- Paths are relative to the **importing file** and must end in `.psd`.
  A `std/` prefix resolves inside the compiler's bundled standard library
  (`import "std/math.psd" as math`).
- `import` gives a **namespace**: call `g.hello()` / `g.hello("x")`. Every
  function of the imported file is reachable this way — a module's functions
  are its interface.
- `from ... import` binds names into the importing file's namespace; the
  names must not collide with local functions (compile error).
- Function names are file-scoped: two modules may both define `helper()`.
  An alias may not collide with a function name in the same file.
- Diamond imports load once; import **cycles are a compile error**.
- Imported functions are ordinary functions (same signature rules, same
  return-type inference; annotate return types of exported functions).

```psd
# lib/greet.psd
fn hello(name: str) -> str {
    return "Hello, " + name
}
```
```psd
# main.psd
import "lib/greet.psd" as g

fn main() {
    print(g.hello("world"))
}
```

## 4. Statements

```text
let x = expr              # declare + initialize (type inferred)
let x: Type = expr        # declare with annotation
x = expr                  # assign (type must match)
x += expr                 # also -=  *=  /=  %=   (str supports +=)
expr                      # expression statement (e.g. a call)
return                    # return from a void function
return expr               # return a value
break / continue          # exit / next-iteration of the innermost loop
{ ... }                   # bare block (introduces a scope)
```

Blocks introduce scopes; shadowing an outer name is allowed.

### Conditionals

```text
if cond { ... } elif cond { ... } else { ... }    # elif == else if
```

`cond` must be `bool` **or `json`**:

- typed values (`int`, `str`, lists, maps) are **not** implicitly truthy —
  `if 5` is a compile error; write `if 5 > 0`.
- `json` conditions use **Python-like truthiness**: `null`, `false`, `0`,
  `0.0`, `""`, `[]`, `{}` are falsy; other values are truthy.

### Loops

```text
while cond { ... }
for x in range(a, b) { ... }    # x: int, a..b-1
for x in range(n) { ... }       # 0..n-1
for x in listExpr { ... }       # x: element type
for x in jsonExpr { ... }       # x: json (arrays: elements, objects: values)
for k in mapOrJson.keys() { ... }
```

`break` and `continue` refer to the innermost loop. Iterating a list or json
evaluates the expression **once**.

## 5. Expressions

Precedence, low to high:

1. `or` / `||`
2. `and` / `&&`
3. `not` / `!` (unary)
4. `== != < <= > >=`
5. `+ -`
6. `* / %`
7. unary `-`
8. call `f(...)`, index `e[i]`, method `e.name(...)`
9. literals, names, `(expr)`, list literals `[e, ...]`, map literals `{k: v}`

Semantics:

- `/` and `%` on `int` are **truncating** (C semantics); division/modulo by
  zero is a clean runtime error. `%` on floats is `frem` (C `fmod`).
- `+` on `str` concatenates; `+=` works on `str`.
- `==` / `!=` work on `int`, `float`, `bool`, `str`, and `json` (see the
  json table above). Ordering (`<` etc.) works on `int`, `float` only.
- `and` / `or` are **short-circuiting** and require `bool` operands.
- Indexing: `list[i]`, `map[key]`, `json[keyOrIndex]`. List and map indexing
  is bounds/key-checked at runtime (clean error, never memory corruption).
- `e[i] = v` and `e[i] += v` are allowed on lists and maps (compound ops not
  supported on `json` — convert, then assign).
- An empty list/map literal needs a type annotation
  (`let x: [int] = []`), except when nested inside a typed context.
- Map literals accept string or identifier keys: `{"a": 1, b: 2}`.

### Method calls

`expr.name(args)` dispatches on the receiver type:

| Receiver | Methods |
|---|---|
| `str` | `upper lower trim contains starts_with ends_with replace(a,b) split(sep) repeat(n) len to_int to_float` |
| `[T]` | `push(v) pop() contains(v) reverse() len() join(sep)` |
| `{str: T}` | `keys() values() has(k) remove(k) get(k[, default]) len()` |
| `json` | `keys() has(k) len() push(v)` |

## 6. String interpolation

f-strings (`f"...{expr}..."`) convert `expr` to `str` (`int`, `float`,
`bool`, `str`, `json`) and concatenate. `{{` / `}}` produce literal braces.
Plain strings never interpolate.

## 7. Builtins

| Call | Signature | Notes |
|---|---|---|
| `print(x, ...)` | any mix of `int/float/bool/str/json` | space-separated + newline |
| `len(x)` | `str \| [T] \| {str: T} \| json -> int` | |
| `push(l, v)` | `([T], T) -> void` | also `json.push(v)` |
| `pop(l)` | `[T] -> T` | error on empty |
| `str(x)` | `int/float/bool/str/json -> str` | json: display form |
| `int(x)` | `int/float/bool/str/json -> int` | `int("42")`; float truncates |
| `float(x)` | `int/float/bool/str/json -> float` | |
| `range(a, b)` / `range(n)` | `int -> range` | only in `for ... in` |
| `read_file(path)` | `str -> str` | errors if missing |
| `write_file(path, s)` / `append_file(path, s)` | `(str, str) -> void` | |
| `exists(path)` / `delete(path)` | `str -> bool` / `-> void` | |
| `list_dir(path)` | `str -> [str]` | directory entries (excluding `.` / `..`) |
| `run([prog, arg, ...])` | `[str] -> int` | argv-based, **no shell** (safe with untrusted values) |
| `capture([prog, arg, ...])` | `[str] -> str` | argv-based, **no shell**; captures **stdout** (stderr passes through to the console); panics on non-zero exit |
| `shell(cmd)` | `str -> int` | **explicit shell** — metacharacters ARE interpreted |
| `capture_shell(cmd)` | `str -> str` | explicit shell; captures **stdout** (stderr passes through); panics on non-zero exit |
| `try_read_file(path)` | `str -> json` | recoverable `read_file` (see below) |
| `try_write_file(path, text)` | `str, str -> json` | recoverable `write_file` |
| `try_int(s)` | `str -> json` | recoverable `int()` on strings |
| `try_json_parse(s)` | `str -> json` | recoverable `json_parse` |
| `try_http_get(url)` | `str -> json` | recoverable `http_get` (needs `curl`) |
| `try_capture([prog, ...][, secs])` | `[str], [int] -> json` | recoverable `capture`; optional timeout (kills the process; `code` = -1) |
| `try_capture_shell(cmd[, secs])` | `str, [int] -> json` | recoverable `capture_shell` with optional timeout |
| `html_escape(s)` | `str -> str` | escapes `& < > " '` for safe HTML output |
| `url_encode(s)` / `url_decode(s)` | `str -> str` | percent-encoding; `url_decode` maps `+` to space (form semantics) |
| `sqrt(x)` | `int\|float -> float` | square root |
| `exp(x)` / `log(x)` / `pow(a, b)` | `float -> float` | exponentials for ML math |
| `sleep_ms(ms)` / `time_ms()` | `int -> void` / `-> int` | timing (games, benches, polling) |
| `random()` / `rand_int(n)` | `-> float` / `int -> int` | [0,1) float / [0,n) int |
| `term_clear()` / `term_home()` | `-> void` | ANSI terminal control |
| `read_key()` | `-> str` | one key, raw TTY: arrows -> "up"/"down"/"left"/"right", Enter -> "enter", ESC/EOF -> "esc"/"" |
| `term_size()` | `-> json` | `{"cols": int, "rows": int}` (fallback 80x24) |
| `open_url(url)` | `str -> int` | open the system browser (1 ok / 0 failed; process capability) |
| `image_info(path)` | `str -> json` | `{ok,width,height,format}` or error envelope (PPM/BMP) |
| `image_process(in, out, op, arg)` | `str,str,str,int -> json` | ops: grayscale, invert, brightness(%), flip_h, flip_v, blur, resize(new width) |
| `http_get(url)` | `str -> str` | body; panics on non-2xx |
| `http_post(url, body, ct)` | `(str, str, str) -> str` | body; panics on non-2xx |
| `http_request(method, url, body, headers)` | `-> json` | `{"status": int, "ok": bool, "body": str}`; headers: `{str: str}` |
| `http_listen(port[, host])` | `int, [str] -> int` | start the built-in HTTP **server**; returns the bound port (`0` = ephemeral). Host defaults to `127.0.0.1` |
| `http_accept()` | `-> json` | block until a request: `{"method", "path", "query", "body", "headers"}` (path URL-decoded; headers is a json object, names lowercased, last value wins) |
| `http_reply(status, content_type, body)` | `int, str, str -> void` | answer the pending request and close it |
| `http_redirect(url)` | `str -> void` | answer the pending request with 302 + `Location: url` |
| `exp(x)` / `log(x)` | `int\|float -> float` | `e^x` / natural log (IEEE domain semantics: NaN/-inf, no panic) |
| `pow(x, y)` | `(int\|float, int\|float) -> float` | `x^y` |
| `sleep_ms(ms)` | `int -> void` | sleep at least `ms` milliseconds |
| `time_ms()` | `-> int` | wall-clock epoch milliseconds |
| `random()` | `-> float` | uniform in `[0, 1)` (non-cryptographic) |
| `rand_int(n)` | `int -> int` | uniform in `0..n-1`; errors when `n <= 0` |
| `read_line()` / `read_line(prompt)` | `[str] -> str` | one stdin line, newline stripped; `""` at EOF; prompt flushed to stdout |
| `cwd()` | `-> str` | current working directory |
| `make_dir(path)` | `str -> void` | create a directory and missing parents; succeeds if it already exists |
| `try_make_dir(path)` | `str -> json` | recoverable `make_dir` |
| `try_float(s)` | `str -> json` | recoverable `float()` on strings |
| `try_run([prog, ...][, secs])` | `[str], [int] -> json` | recoverable `run`; envelope `{ok, code}` (`code` -1 on timeout) |
| `json_parse(s)` / `json_encode(v)` | `str <-> json` | strict JSON |
| `args()` | `-> [str]` | arguments after the program name |
| `env(name)` / `env(name, default)` | `-> str` | errors if unset (no default) |
| `exit(code)` | `int -> !` | terminates |

### Process execution and the trust boundary

PSD draws an explicit line between safe and unsafe process execution:

- `run([...])` / `capture([...])`) take an **argv list** and execute the
  program directly (`fork` + `execvp`) with **no shell**. Arguments are never
  interpreted, so untrusted values (filenames, user input) are safe to pass.
- `shell(cmd)` / `capture_shell(cmd)` pass `cmd` to the **system shell**
  (`system` / `popen`). Metacharacters (`|`, `;`, `$`, backticks, ...) **are**
  interpreted. Never build these strings from untrusted input.

**Capability switches** — environment variables disable builtin groups at
runtime (the builtin fails with a runtime error):

| Variable | Blocks |
|---|---|
| `PSD_DISABLE_PROCESS=1` | `run`, `capture`, `shell`, `capture_shell`, `try_capture`, `try_capture_shell`, `try_run` |
| `PSD_DISABLE_HTTP=1` | `http_get`, `http_post`, `http_request`, `http_listen`, `http_accept`, `http_reply`, `http_redirect`, `try_http_get` |
| `PSD_DISABLE_FILES=1` | `read_file`, `write_file`, `append_file`, `exists`, `delete`, `list_dir`, `try_read_file`, `try_write_file`, `make_dir`, `try_make_dir` |

⚠️ These switches are **not a sandbox**. They disable *specific builtins*,
not capabilities of the process: with `PSD_DISABLE_FILES=1` a program can
still read and write files through `shell("cat /etc/passwd")`. To run
**untrusted** PSD programs, combine (a) an OS-level sandbox (container, VM,
jail, seccomp) with (b) disabling every relevant capability switch. The
switches are defense-in-depth and a way to shape trusted code, not isolation.

### Recoverable errors

`try_*` builtins never terminate the program for ordinary failures. They
return a **json result envelope** with exactly this shape:

```text
{"ok": true,  "value": <result>}
{"ok": false, "error": "<message>"}       # plus extras where useful:
# try_capture / try_capture_shell errors add "code" (exit status, -1 on
#   timeout) and "value" (stdout captured so far)
# try_http_get errors add "status" and "value" when the server answered
```

Field access is `r["ok"]` (json, truthy), `str(r["value"])`,
`str(r["error"])`. The panicking builtins (`read_file`, `capture`, ...) keep
their fail-loudly contract; `try_*` are the recoverable counterparts.

### Web serving

The HTTP server handles **one request at a time** (sequential, no
concurrency), speaks HTTP/1.1 with `Connection: close`, caps requests at
64 KiB of headers and 8 MiB of body, and supports no TLS, no chunked
uploads, and no WebSockets. `http_listen` binds `127.0.0.1` unless a host
is given. Undelivered requests are closed when the next `http_accept`
begins. This is a foundation for local tools and internal interfaces;
internet-facing deployments need a reverse proxy and OS-level hardening.

## 8. Evaluation order and memory

- Operands evaluate left to right; assignment evaluates the right side first.
- `str` and `[T]` values are heap objects with **automatic reference
  counting** (Swift-style). The compiler inserts all increfs/decrefs;
  there is no GC, no manual free, no borrow checker.
- Strings are immutable and shared between variables safely.
- Values are semantically copied on assignment/binding (sharing under the
  hood, with reference counts).
- Runtime errors (index out of range, division by zero, bad conversions,
  empty `pop`) print `psd: runtime error: <msg>` to stderr and exit with
  status 1. They never corrupt memory.

## 9. Edge-case semantics

These behaviors are part of the language contract and are covered by tests.

**Integers** (`int` is 64-bit two's complement):
- `+`, `-`, `*` **wrap** on overflow (like C, Rust release mode). Integer
  literals outside the 64-bit range also wrap to fit, so
  `-9223372036854775808` spells `INT_MIN`.
- `/` truncates toward zero (`7 / 2 == 3`, `-7 / 2 == -3`).
- `%` follows the dividend's sign (`-7 % 2 == -1`, `7 % -2 == 1`, C rules).
- Division or modulo by zero is a **runtime error**; `INT_MIN / -1` is a
  runtime error (it would overflow).

**Floats** (`float` is IEEE-754 binary64):
- Division by zero yields `inf` / `-inf`; `0.0 / 0.0` yields `nan` — no panics.
- `==` is ordered: `nan == nan` is false. `!=` is unordered: `nan != nan` is
  true (Python/C behavior).
- `0.1 + 0.2 == 0.3` is false (binary floating point, as in Python).
- `int(f)` truncates toward zero; values that do not fit in a 64-bit int
  (including `inf`, `-inf`, `nan`) are a **checked runtime error**
  (`int(): float out of int range` / `int(): cannot convert nan to int`).

**Strings**: `len(s)` and all string methods are **byte-oriented** (UTF-8).
`len("héllo")` is 6. There is no character indexing. Case methods
(`upper`/`lower`) are ASCII-only.

**The runtime error contract**: every checked failure (index out of range,
missing map key, empty `pop`, division by zero, bad `int()`/`float()`
conversion, invalid `json_parse`, missing file, failed command) prints
`psd: runtime error: <message>` to stderr and exits with status 1.
A PSD program never performs undefined behavior, corrupts memory, or
segfaults on bad input — it either computes or fails loudly.

**Strings and containers are immutable where noted**: `str` values never
change; lists/maps are mutated in place and shared copies see the change
(a read of a list/map variable produces a shared reference, not a deep copy).

## 10. Errors

All lexical, syntactic and type errors are reported **before** execution with
line and column numbers, e.g.:

```
prog.psd: error: condition must be bool, got int (line 3, col 8)
```

## 11. Compilation

```
psd run f.psd            compile + execute
psd build f.psd [-o b]   compile to a native binary
psd check f.psd          front-end only
psd emit-llvm f.psd      write f.ll
```

The compiler emits LLVM IR, optimizes it with LLVM (O3 pipeline:
mem2reg, instcombine, loop vectorization, ...), and links the object with the
portable C11 runtime (`psd/runtime.c`) using any C compiler. The `.ll` file
can alternatively be compiled with `clang file.ll psd/runtime.c -O3`.
