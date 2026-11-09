"""Machine-readable PSD catalog (`psd doc`, `psd guide`).

One queryable source for "what can I call?" so an AI agent never has to
scrape prose docs: `psd doc --json` emits every builtin and stdlib
function with its signature. The test suite asserts this catalog matches
the checker (BUILTIN_NAMES) and the actual std/*.psd function sets, so
it cannot drift from the language.

Each builtin: (name, signature, returns, group, one-line description).
"""

from __future__ import annotations

VERSION = "0.6"

TYPES = ["int", "float", "str", "bool", "json", "[T]", "{str: T}", "void"]

QUICK_RULES = [
    "Statements end at a newline; blocks use { }. Comments: # or //.",
    "Strings are double-quoted and literal; interpolate with f\"... {expr} ...\" ({{ }} = literal braces).",
    "let x = 5 infers int; params always need types: fn add(a: int, b: int) -> int.",
    "Conditions must be bool (if x > 0) — except json, which is Python-truthy.",
    "Mixed literals are json; homogeneous literals are typed ([int], {str: int}). Convert json before arithmetic: int(j[\"n\"]) + 1.",
    "Empty literals need annotation: let a: [int] = []  /  let m: json = {}.",
    "Define functions before use, or annotate their return type. fn main() is the entry point.",
    "run/capture take argv lists (no shell, safe); shell/capture_shell are the explicit shell.",
    "Ordinary failures: use try_* builtins — they return {\"ok\": bool, \"value\"/\"error\": ...} instead of crashing.",
    "Loop fast: psd check --json f.psd (structured errors) -> fix -> psd run f.psd (cached build). psd doc --json lists every callable; psd fmt normalizes layout.",
]

SKELETON = '''fn greet(name: str) -> str {
    return f"Hello, {name}!"
}

fn main() {
    let nums = [1, 2, 3]
    for n in nums {
        print(greet("world"), n)
    }
}
'''

BUILTINS = [
    # core
    ("print", "print(a, b, ...)", "void", "core", "print values space-separated + newline"),
    ("len", "len(x)", "int", "core", "length of str, list, map or json"),
    ("push", "push(list, v)", "void", "core", "append to a list (or json array)"),
    ("pop", "pop(list)", "T", "core", "remove + return last element"),
    ("str", "str(x)", "str", "core", "convert int/float/bool/str/json to str"),
    ("int", "int(x)", "int", "core", "convert to int (float truncates; str parses)"),
    ("float", "float(x)", "float", "core", "convert to float"),
    ("range", "range(n) | range(a, b)", "range", "core", "integer range, only in for loops"),
    ("exit", "exit(code)", "void", "core", "terminate with exit code"),
    # math
    ("sqrt", "sqrt(x)", "float", "math", "square root"),
    ("exp", "exp(x)", "float", "math", "e^x (ML: sigmoid/softmax)"),
    ("log", "log(x)", "float", "math", "natural log (IEEE semantics)"),
    ("pow", "pow(x, y)", "float", "math", "x^y"),
    # strings / web text
    ("html_escape", "html_escape(s)", "str", "text", "escape & < > \" ' for HTML"),
    ("url_encode", "url_encode(s)", "str", "text", "percent-encode"),
    ("url_decode", "url_decode(s)", "str", "text", "percent-decode (+ = space)"),
    # json
    ("json_parse", "json_parse(s)", "json", "json", "parse strict JSON"),
    ("json_encode", "json_encode(v)", "str", "json", "encode any value as JSON text"),
    # files
    ("read_file", "read_file(path)", "str", "files", "read a whole file (errors if missing)"),
    ("write_file", "write_file(path, s)", "void", "files", "write (truncate/create)"),
    ("append_file", "append_file(path, s)", "void", "files", "append to a file"),
    ("exists", "exists(path)", "bool", "files", "does the path exist?"),
    ("delete", "delete(path)", "void", "files", "delete a file"),
    ("list_dir", "list_dir(path)", "[str]", "files", "directory entries (no . / ..)"),
    ("make_dir", "make_dir(path)", "void", "files", "create directory and parents; ok if it exists"),
    ("cwd", "cwd()", "str", "files", "current working directory"),
    # process
    ("run", "run([prog, arg, ...])", "int", "process", "run, return exit code (argv, NO shell)"),
    ("capture", "capture([prog, arg, ...])", "str", "process", "run, return stdout (argv, NO shell)"),
    ("shell", "shell(cmd)", "int", "process", "run via system shell (trusted strings only)"),
    ("capture_shell", "capture_shell(cmd)", "str", "process", "stdout via system shell (trusted only)"),
    ("args", "args()", "[str]", "process", "command-line args after program name"),
    ("env", "env(name[, default])", "str", "process", "environment variable"),
    ("read_line", "read_line([prompt])", "str", "process", "one stdin line ('' at EOF)"),
    ("sleep_ms", "sleep_ms(ms)", "void", "process", "sleep at least ms milliseconds"),
    ("time_ms", "time_ms()", "int", "process", "wall-clock epoch milliseconds"),
    ("random", "random()", "float", "process", "uniform float in [0, 1)"),
    ("rand_int", "rand_int(n)", "int", "process", "uniform int in 0..n-1"),
    # http client
    ("http_get", "http_get(url)", "str", "http", "GET body (needs curl; errors on non-2xx)"),
    ("http_post", "http_post(url, body, content_type)", "str", "http", "POST body"),
    ("http_request", "http_request(method, url, body, headers)", "json", "http", "{status, ok, body} full-control request"),
    # http server
    ("http_listen", "http_listen(port[, host])", "int", "server", "bind server (127.0.0.1 default), return bound port"),
    ("http_accept", "http_accept()", "json", "server", "next request {method, path, query, body, headers}"),
    ("http_reply", "http_reply(status, content_type, body)", "void", "server", "answer pending request"),
    ("http_redirect", "http_redirect(url)", "void", "server", "302 redirect to url"),
    ("http_listen_pool", "http_listen_pool(port, workers | port, host, workers)", "int", "server", "prefork N worker processes on one socket (concurrent serving)"),
    ("http_reply_file", "http_reply_file(path, content_type)", "void", "server", "stream a file as the response (404 when missing)"),
    # terminal / desktop / games / images
    ("term_clear", "term_clear()", "void", "terminal", "clear terminal (ANSI)"),
    ("term_home", "term_home()", "void", "terminal", "move cursor home (ANSI)"),
    ("read_key", "read_key()", "str", "terminal", "one key: arrows up/down/left/right, enter, esc, '' at EOF"),
    ("term_size", "term_size()", "json", "terminal", "{cols, rows} of the terminal (80x24 fallback)"),
    ("open_url", "open_url(url)", "int", "terminal", "open system browser at url (1 ok / 0 failed)"),
    ("image_info", "image_info(path)", "json", "images", "{ok, width, height, format} for PPM/BMP"),
    ("image_process", "image_process(in, out, op, arg)", "json", "images", "grayscale/invert/brightness/flip_h/flip_v/blur/resize (PPM/BMP)"),
    # recoverable (try_*)
    ("try_read_file", "try_read_file(path)", "json", "try", "{ok, value|error} read_file"),
    ("try_write_file", "try_write_file(path, s)", "json", "try", "{ok, value|error} write_file"),
    ("try_int", "try_int(s)", "json", "try", "{ok, value|error} int parse"),
    ("try_float", "try_float(s)", "json", "try", "{ok, value|error} float parse"),
    ("try_json_parse", "try_json_parse(s)", "json", "try", "{ok, value|error} json parse"),
    ("try_http_get", "try_http_get(url)", "json", "try", "{ok, value|error} (+status on HTTP errors)"),
    ("try_capture", "try_capture([prog, ...][, timeout_secs])", "json", "try", "{ok, value|error, code} capture"),
    ("try_capture_shell", "try_capture_shell(cmd[, timeout_secs])", "json", "try", "{ok, value|error, code} shell capture"),
    ("try_make_dir", "try_make_dir(path)", "json", "try", "{ok, value|error} make_dir"),
    ("try_run", "try_run([prog, ...][, timeout_secs])", "json", "try", "{ok, code} recoverable run"),
]

# stdlib: module -> [(name, signature, description)]  (validated by tests)
STDLIB = {
    "std/math.psd": [
        ("min", "min(a: int, b: int) -> int", "smaller of two ints"),
        ("max", "max(a: int, b: int) -> int", "larger of two ints"),
        ("abs", "abs(x: int) -> int", "absolute value (int)"),
        ("sign", "sign(x: int) -> int", "-1, 0 or 1"),
        ("clamp", "clamp(x: int, lo: int, hi: int) -> int", "clamp into [lo, hi]"),
        ("powi", "powi(base: int, exp: int) -> int", "integer power"),
        ("absf", "absf(x: float) -> float", "absolute value (float)"),
        ("minf", "minf(a: float, b: float) -> float", "smaller of two floats"),
        ("maxf", "maxf(a: float, b: float) -> float", "larger of two floats"),
        ("approx", "approx(a: float, b: float, tol: float) -> bool", "|a-b| <= tol"),
    ],
    "std/ml.psd": [
        ("fit", "fit(xs: [float], ys: [float]) -> json", "least-squares y = w*x + b"),
        ("predict", "predict(m: json, x: float) -> float", "scalar inference"),
        ("loss", "loss(m: json, xs: [float], ys: [float]) -> float", "mean squared error (scalar model)"),
        ("save", "save(path: str, m: json) -> json", "persist model json"),
        ("load", "load(path: str) -> json", "load model json ({ok, value})"),
        ("fit_multi", "fit_multi(xs: [[float]], ys: [float], lr: float, epochs: int) -> json", "multivariate regression (gradient descent)"),
        ("predict_multi", "predict_multi(m: json, x: [float]) -> float", "multivariate inference"),
        ("loss_multi", "loss_multi(m: json, xs: [[float]], ys: [float]) -> float", "MSE (multivariate)"),
        ("fit_logistic", "fit_logistic(xs: [[float]], ys: [float], lr: float, epochs: int) -> json", "binary logistic classifier"),
        ("predict_proba", "predict_proba(m: json, x: [float]) -> float", "P(label=1)"),
        ("predict_label", "predict_label(m: json, x: [float]) -> int", "0 or 1 at threshold 0.5"),
        ("accuracy", "accuracy(m: json, xs: [[float]], ys: [float]) -> float", "fraction of correct labels"),
        ("sigmoid", "sigmoid(x: float) -> float", "1 / (1 + e^-x)"),
        ("dot", "dot(w: [float], x: [float]) -> float", "dot product"),
        ("weights_of", "weights_of(m: json) -> [float]", "model weights as [float]"),
        ("mean", "mean(xs: [float]) -> float", "arithmetic mean"),
        ("sum", "sum(xs: [float]) -> float", "sum of elements"),
        ("stddev", "stddev(xs: [float]) -> float", "population standard deviation"),
        ("sum_dev_sq", "sum_dev_sq(xs: [float], m: float) -> float", "sum of squared deviations"),
        ("sum_dev_prod", "sum_dev_prod(xs: [float], ys: [float], mx: float, my: float) -> float", "sum of deviation products"),
        ("rmse", "rmse(m: json, xs: [float], ys: [float]) -> float", "root mean squared error"),
        ("r2", "r2(ys: [float], preds: [float]) -> float", "coefficient of determination"),
        ("fit_scaler", "fit_scaler(xs: [[float]]) -> json", "per-column mean/std"),
        ("apply_scaler", "apply_scaler(xs: [[float]], scaler: json) -> [[float]]", "standardize features"),
        ("train_test_split", "train_test_split(xs: [float], ys: [float], k: int) -> json", "deterministic every-kth split"),
    ],
    "std/data.psd": [
        ("parse_csv", "parse_csv(text: str) -> json", "CSV text -> rows of [str]"),
        ("csv_numbers", "csv_numbers(text: str, skip_header: bool) -> json", "CSV -> {ok, value: [[float]]}"),
        ("load_numbers", "load_numbers(path: str, skip_header: bool) -> json", "numeric CSV file -> matrix envelope"),
        ("to_matrix", "to_matrix(rows: json) -> [[float]]", "json matrix -> typed matrix"),
        ("column", "column(rows: [[float]], j: int) -> [float]", "one column as [float]"),
        ("features", "features(rows: [[float]], skip: int) -> [[float]]", "all columns except j"),
        ("chat_example", "chat_example(system: str, user: str, assistant: str) -> json", "fine-tune chat example"),
        ("instruction_example", "instruction_example(instruction: str, output: str) -> json", "fine-tune instruction example"),
        ("save_jsonl", "save_jsonl(path: str, rows: [json]) -> json", "write JSONL dataset"),
        ("load_jsonl", "load_jsonl(path: str) -> json", "read JSONL dataset"),
    ],
    "std/agent.psd": [
        ("system_msg", "system_msg(content: str) -> json", "{\"role\": \"system\", ...}"),
        ("user_msg", "user_msg(content: str) -> json", "{\"role\": \"user\", ...}"),
        ("assistant_msg", "assistant_msg(content: str) -> json", "{\"role\": \"assistant\", ...}"),
        ("tool_result", "tool_result(call_id: str, content: str) -> json", "tool reply message"),
        ("tool_spec", "tool_spec(name: str, description: str, parameters: json) -> json", "function-calling tool schema"),
        ("string_params", "string_params(names: [str]) -> json", "JSON schema for string params"),
        ("chat_payload", "chat_payload(model: str, messages: [json], tools: [json]) -> json", "chat-completions request body"),
        ("chat", "chat(base_url: str, api_key: str, payload: json) -> json", "POST to an OpenAI-compatible API"),
        ("reply_content", "reply_content(resp: json) -> str", "assistant text of first choice"),
        ("tool_calls", "tool_calls(resp: json) -> json", "[{id, name, args}] of first choice"),
        ("wants_tools", "wants_tools(resp: json) -> bool", "did the model request tools?"),
    ],
    "std/web.psd": [
        ("esc", "esc(s: str) -> str", "html_escape alias"),
        ("page", "page(title: str, body: str) -> str", "full HTML page shell"),
        ("h1", "h1(text: str) -> str", "escaped <h1>"),
        ("h2", "h2(text: str) -> str", "escaped <h2>"),
        ("p", "p(text: str) -> str", "escaped <p>"),
        ("link", "link(href: str, text: str) -> str", "escaped <a>"),
        ("nav", "nav(links: [str], labels: [str]) -> str", "<nav> of links"),
        ("list_items", "list_items(items: [str]) -> str", "<ul> of escaped items"),
        ("table", "table(headers: [str], rows: json) -> str", "<table> from json rows"),
        ("form_open", "form_open(action: str) -> str", "<form method=post>"),
        ("form_close", "form_close() -> str", "</form>"),
        ("text_input", "text_input(name: str, placeholder: str) -> str", "text <input>"),
        ("submit_button", "submit_button(label: str) -> str", "submit <button>"),
        ("parse_form", "parse_form(body: str) -> json", "urlencoded body/query -> json object"),
        ("form_value", "form_value(body: str, key: str) -> str", "one form field ('' if absent)"),
        ("reply_html", "reply_html(status: int, body: str)", "http_reply text/html"),
        ("reply_json", "reply_json(status: int, value: json)", "http_reply application/json"),
        ("reply_page", "reply_page(title: str, body: str)", "200 page() reply"),
        ("not_found", "not_found(what: str)", "404 page reply"),
        ("redirect_to", "redirect_to(url: str)", "302 redirect reply"),
        ("page_styled", "page_styled(title: str, css: str, body: str) -> str", "responsive page with embedded CSS (mobile/PWA)"),
        ("pwa_manifest", "pwa_manifest(name: str, short_name: str, theme_color: str) -> str", "PWA manifest json string"),
        ("button_link", "button_link(href: str, label: str) -> str", "<a class=btn>"),
        ("input_field", "input_field(name: str, label: str, value: str) -> str", "labelled text <input>"),
        ("select_field", "select_field(name: str, label: str, options: [str]) -> str", "labelled <select>"),
        ("card", "card(title: str, body_html: str) -> str", "<section class=card>"),
        ("table_html", "table_html(headers: [str], rows: [[str]]) -> str", "<table> from typed rows"),
        ("q1", "q1(req: json, name: str) -> str", "first query value ('' if absent)"),
        ("base_css", "base_css() -> str", "fluid responsive CSS, 44px touch targets"),
    ],
    "std/numeric.psd": [
        ("zeros", "zeros(rows: int, cols: int) -> [[float]]", "zero matrix"),
        ("identity", "identity(n: int) -> [[float]]", "identity matrix"),
        ("matmul", "matmul(a: [[float]], b: [[float]]) -> [[float]]", "matrix product"),
        ("transpose", "transpose(a: [[float]]) -> [[float]]", "matrix transpose"),
        ("matvec", "matvec(a: [[float]], x: [float]) -> [float]", "matrix x vector"),
        ("dot_vec", "dot_vec(a: [float], b: [float]) -> float", "vector dot product"),
        ("add_vec", "add_vec(a: [float], b: [float]) -> [float]", "vector sum"),
        ("sigmoid_vec", "sigmoid_vec(a: [float]) -> [float]", "elementwise sigmoid"),
        ("tanh_vec", "tanh_vec(a: [float]) -> [float]", "elementwise tanh"),
        ("scale_mat", "scale_mat(a: [[float]], s: float) -> [[float]]", "scalar multiply"),
    ],
    "std/nn.psd": [
        ("make_mlp", "make_mlp(sizes: [int], seed: int) -> json", "seeded MLP model {sizes, weights, biases}"),
        ("train", "train(m: json, xs: [[float]], ys: [[float]], lr: float, epochs: int) -> json", "backprop SGD training -> {ok, loss, weights, biases}"),
        ("predict", "predict(m: json, x: [float]) -> [float]", "MLP forward pass"),
        ("loss", "loss(m: json, xs: [[float]], ys: [[float]]) -> float", "dataset MSE"),
        ("forward_acts", "forward_acts(m: json, x: [float]) -> [[float]]", "activations per layer"),
        ("rng_new", "rng_new(seed: int) -> [int]", "seeded LCG state box"),
        ("rng_float", "rng_float(state: [int]) -> float", "next LCG float in [0, 1)"),
        ("mat_to_json", "mat_to_json(m: [[float]]) -> json", "matrix -> json"),
        ("json_to_mat", "json_to_mat(v: json) -> [[float]]", "json -> matrix"),
        ("vec_to_json", "vec_to_json(v: [float]) -> json", "vector -> json"),
    ],
    "std/game.psd": [
        ("grid_new", "grid_new(w: int, h: int) -> [[str]]", "'.'-filled character grid"),
        ("grid_set", "grid_set(g: [[str]], x: int, y: int, ch: str)", "bounds-checked cell write"),
        ("grid_get", "grid_get(g: [[str]], x: int, y: int) -> str", "cell read ('' outside)"),
        ("grid_render", "grid_render(g: [[str]]) -> str", "grid as printable text"),
        ("draw_box", "draw_box(g: [[str]], x: int, y: int, w: int, h: int, ch: str)", "rectangle border"),
        ("point", "point(x: int, y: int) -> json", "{x, y} point"),
        ("move", "move(p: json, dx: int, dy: int) -> json", "translated point"),
        ("same_point", "same_point(a: json, b: json) -> bool", "point equality"),
        ("inside", "inside(p: json, w: int, h: int) -> bool", "point within w x h"),
        ("key_dir", "key_dir(key: str) -> [int]", "[dx, dy] for arrows/WASD"),
        ("rng_new", "rng_new(seed: int) -> [int]", "seeded LCG state box"),
        ("rng_int", "rng_int(state: [int], n: int) -> int", "next LCG int in [0, n)"),
        ("rng_float", "rng_float(state: [int]) -> float", "next LCG float in [0, 1)"),
    ],
}


def as_json() -> dict:
    return {
        "language": "PSD",
        "version": VERSION,
        "types": TYPES,
        "rules": QUICK_RULES,
        "builtins": [
            {"name": n, "signature": s, "returns": r, "group": g, "description": d}
            for (n, s, r, g, d) in BUILTINS
        ],
        "stdlib": {
            mod: [{"name": n, "signature": s, "description": d}
                  for (n, s, d) in fns]
            for mod, fns in STDLIB.items()
        },
    }


def find(name: str) -> dict | None:
    """Look up a builtin or stdlib function by bare name."""
    for (n, s, r, g, d) in BUILTINS:
        if n == name:
            return {"kind": "builtin", "name": n, "signature": s,
                    "returns": r, "group": g, "description": d}
    for mod, fns in STDLIB.items():
        for (n, s, d) in fns:
            if n == name:
                return {"kind": "stdlib", "module": mod, "name": n,
                        "signature": s, "description": d,
                        "import": f'import "{mod}" as {mod[4:-4]}'}
    return None


def render_guide() -> str:
    lines = [
        "PSD quick reference (for AI agents — `psd doc --json` has everything)",
        "",
        "Rules:",
    ]
    lines += [f" {i + 1}. {r}" for i, r in enumerate(QUICK_RULES)]
    lines += ["", "Skeleton:", "```psd", SKELETON.rstrip(), "```", "",
              "Builtins by group:"]
    groups: dict[str, list[str]] = {}
    for (n, s, _r, g, _d) in BUILTINS:
        groups.setdefault(g, []).append(s)
    for g, sigs in groups.items():
        lines.append(f" [{g}] " + " · ".join(sigs))
    lines += ["", "Stdlib: " + ", ".join(
        f"{mod} ({len(fns)} fns)" for mod, fns in STDLIB.items()),
        "Details: psd doc <name> · psd doc --json · AI_GUIDE.md · docs/RATING.md"]
    return "\n".join(lines) + "\n"
