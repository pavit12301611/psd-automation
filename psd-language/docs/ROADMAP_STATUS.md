# PSD — Capability Report & Phased Plan (2026-11-05)

## Current capabilities (verified against the checkout)

- **Language**: Python-like syntax; static types `int` `float` `str` `bool` `json`
  `list[T]` `map[K,V]`; f-strings; str methods; `json` with Python-style
  truthiness + `==`/`!=`; checked runtime-error contract (fail loudly, never UB).
- **Modules**: `import "path.psd" [as a]` + `from "path.psd" import f`; file-scoped
  namespaces; cycles are a compile error; bundled `std/math.psd`.
- **Compiler**: Python frontend -> LLVM (llvmlite, O3) -> native binary; C11
  runtime with refcounted str/list/map/json; UAF/leak clean under ASan/LSan.
- **Automation**: files, argv processes (`run`/`capture`, injection-safe),
  explicit `shell`/`capture_shell`, HTTP client via curl, `args`/`env`/`exit`,
  recoverable `try_*` builtins (`try_read_file`, `try_write_file`, `try_int`,
  `try_json_parse`, `try_http_get`, `try_capture`, `try_capture_shell` with
  optional timeout seconds), capability switches
  (`PSD_DISABLE_PROCESS/HTTP/FILES` — switches, NOT a sandbox).
- **Web**: minimal HTTP/1.1 server in the runtime (POSIX sockets):
  `http_listen(port[, host]) -> int` (binds 127.0.0.1 by default, returns the
  bound port), `http_accept() -> json {method, path, query, body}`,
  `http_reply(status, content_type, body)`, plus `html_escape`,
  `url_encode`, `url_decode`. Sequential requests, no TLS, localhost default.
- **Tiny ML**: `psd/std/ml.psd` (pure PSD): ordinary least squares `y = w*x + b`
  on `[float]`, `predict`, `save`/`load` of parameters via json files. Scalar
  CPU-side models only — no tensors, no GPU, no autodiff. Real training is
  delegated to an external runtime over json files (`examples/ml_interop.psd`).
- **Quality**: 50 test programs (19 success + 16 compile-error + 15
  runtime-error), docs-snippet validation (24 psd snippets, 8 type-checked),
  live HTTP server integration test, ASan/LSan suite, reproducible bench
  runner, examples. CI: ubuntu + macos, suite + ASan + examples + bench.

## What PSD does NOT claim (honest limits)

- **No general AI training**: `std/ml` fits one scalar line; it is not a
  neural-network, tensor, GPU, or autodiff framework and has no framework
  compatibility. Pass rates for AI models are not claimed — none were measured.
- **No production security**: capability switches disable builtins only; they
  are not a sandbox. Run untrusted programs in an OS-level sandbox (container,
  VM, jail, seccomp) with all relevant switches disabled.
- **No production web serving**: the built-in server is sequential HTTP/1.1,
  binds 127.0.0.1 by default, has no TLS/chunked/WebSocket support — put a
  reverse proxy in front for anything beyond local tools.

## Remaining roadmap (not started)

1. Windows runtime (process/file layers) so the portability claim is tested.
2. Standard library growth (string extra, time, path) on the builtin layer.
3. Structured records/structs; then self-hosting exploration.
4. Package manager (exploratory).

### Proposed first step toward native numeric arrays

A small, honest slice: `std/numeric.psd` — pure-PSD helpers on nested `[float]`
lists: `dot(x, y)`, `matvec(A, x)`, `matmul(A, B)`, `transpose(A)`, with
dimension mismatches returned as json error envelopes (no panics), plus a
deterministic example (small matrix-based prediction) and a benchmark against
the same computation in Python. Explicitly **not** a tensor library: fixed 2-D
lists only, no broadcasting, no GPU, no autodiff, no framework compatibility.
Success criterion: the example and benchmark run in CI, and the docs say
precisely what it does and does not do. Anything beyond this slice (e.g. a
`Matrix` type) waits until this slice is measured and reviewed.

**Status (2026-11-07): delivered in expanded form** — `psd/std/numeric.psd`
shipped from the capability-expansion branch (zeros/identity/matmul/
transpose/matvec/dot/activations; matrix ops assume valid shapes rather
than returning envelopes), together with `std/nn.psd` MLP training; see
the "Capability Expansion" section below.

## Completed in this patch series

- Bench loop comparison fixed: Python's 1e8 sample is scaled ×10 for both time
  and output; measured vs extrapolated timings are labeled; mismatches exit
  nonzero.
- Docs reconciled: README/SPEC/AI_GUIDE/ROADMAP_STATUS agree on version (v0.3),
  feature set, limitations, and test counts (50 programs / 52 checks).
- AI/ML: deterministic train/evaluate/save/load/predict example
  (`examples/ml_tiny.psd` + `tests/cases/ml_train.psd`); interop path that runs
  an external trainer argv-based with timeout handling, model validation, and
  useful errors (`examples/ml_interop.psd`); new tests for malformed data,
  training failure, timeout, invalid model output, and successful prediction.
- Apps: notes web demo with HTML/CSS UI, PSD backend routes, form validation,
  HTML escaping, JSON API endpoint, empty/error states; HTTP integration tests
  covering normal input, invalid input, XSS payloads, missing routes, API output.
- Automation: new offline example combining JSON, files, processes, and HTTP
  (`examples/healthcheck.psd`) using recoverable builtins only.
- AI agents: agent tasks with executable tests in AI_GUIDE.md; every docs
  ```psd snippet is parse-validated (and type-checked where self-contained) by
  the test suite.

## v0.4 update (2026-11-07) — rating-driven hardening

Full scored audit: `docs/RATING.md` (AI training 3→5, tool-use/fine-tune
5→7.5, GUI 4→6.5, automation 7.5→8.5, AI-writability 7→8).

- **ML training**: `exp/log/pow` builtins; `std/ml` now has `fit_multi`,
  `fit_logistic/predict_proba/predict_label/accuracy`, `sigmoid`, `rmse`,
  `r2`, `fit_scaler/apply_scaler`, `train_test_split`; `std/data`
  (CSV→matrix, JSONL datasets for fine-tuning).
- **Tool-use**: `std/agent` (tool schemas, chat payloads, tool-call
  parsing, tool results); `psd check --json` structured diagnostics.
- **GUI**: `std/web` components + form parsing + json replies;
  runtime request `headers` and `http_redirect`; dashboard example;
  server integration test grew to 8 routes.
- **Automation**: `sleep_ms/time_ms/random/rand_int`, `read_line`,
  `cwd`, `make_dir/try_make_dir`, `try_run`, `try_float`.

Still explicit non-goals: tensors/GPU/autograd, TLS/WebSockets/sessions,
desktop widgets, streaming HTTP, `psd fmt`, glob/regex.

## v0.5 update (2026-11-07) — faster + easier for AI

Goal: shrink the AI generate→check→run→fix loop and let the toolchain
answer its own questions (evidence in docs/RATING.md §5).

- **Build cache**: runtime object + program binaries keyed by content
  hash of every source file + the toolchain (`~/.cache/psd-language`,
  `PSD_CACHE_DIR`, `--no-cache`, `psd cache info/clear`). Warm `psd run`
  measured 0.99s → 0.089s; edited sources always recompile (tested).
- **`psd doc [--json] [name]`**: catalog of all 54 builtins + 76 stdlib
  functions in `psd/catalog.py`, test-asserted in sync with the checker
  and the std/*.psd sources. **`psd guide`** prints the one-screen
  version (10 rules + compiling skeleton).
- **`psd fmt`**: conservative canonical formatter (indent/whitespace
  only, string/comment aware), idempotent + parse-safe on all repo code;
  the repo itself is now fmt-canonical.
- **`psd new --template cli|api|web|train|agent`**: scaffolds that pass
  `psd check` immediately (suite scaffolds + checks all five).
- **Richer diagnostics**: `check --json` adds per-error `hint` +
  `snippet`; lexer/parser/checker now teach the PSD spelling for the
  classic Python-isms (`'quotes'`, `None`/`null`, `list[int]`,
  `input()`, `open()`).
- Tests: 63 passing (was 55), including catalog/fmt/scaffold/cache
  integration checks.

## v0.6 update (2026-11-07) — private AI access

Repo stays private; models get tools or knowledge, never git access
(full playbook: docs/PRIVATE_AI_ACCESS.md).

- **`psd mcp`**: stdlib-only MCP server over stdio — tools `psd_guide`,
  `psd_doc`, `psd_example`, `psd_template`, `psd_check`, `psd_fmt`,
  `psd_run` (temp-dir, timeout, capability-switch sandbox;
  `PSD_MCP_ALLOW_RUN=0` for docs-only) + resources `psd://guide`,
  `psd://catalog`, `psd://ai-guide`, `psd://spec`. Ready configs for
  Claude Desktop/Cursor/VS Code in `integrations/`.
- **`psd bundle [--minimal]`**: generates `dist/psd-ai-bundle.md` from
  live catalog/guide/SPEC/examples (~16k tokens full, ~6k minimal) for
  private ChatGPT/Claude Projects; `dist/` gitignored, no compiler
  source included. Copy-paste `integrations/SYSTEM_PROMPT.md`.
- Tests: 65 passing, incl. a real MCP stdio session (handshake, tools,
  sandbox blocking, kill-switch) and bundle content checks.

GPU/compute arrays, production training, TLS/HTTP2, WebSockets, desktop or
browser UI frameworks, Windows runtime. (Concurrent request handling and
Android app packaging were delivered below — this line is historical.)

## Capability Expansion (2026-11-07) — the five "cannot build" gaps

Native builtins (runtime.c) + checker/codegen + stdlibs + examples +
integration tests, all with honest boundaries in docs/CAPABILITIES.md:

- **Websites at traffic**: `http_listen_pool` (prefork workers on one
  socket), `http_reply_file` (streamed, Content-Length), `http_redirect`,
  request `headers`. Pool integration test runs real concurrent server.
  Still: HTTP/1.1, no TLS/WebSockets (reverse proxy in front).
- **AI training**: `exp/log/pow` + `std/numeric.psd` (matrices) +
  `std/nn.psd` (MLP backprop; XOR test learns + saves json model).
  Bench: ~357 ms PSD vs ~749 ms pure Python (identical algorithm).
  Still: no tensors/GPU/deep learning — interop via `run()` documented.
- **Games**: `term_clear/home/size`, blocking `read_key` (arrows
  normalized), `sleep_ms/time_ms/random/rand_int`, `std/game.psd`,
  playable `examples/game_snake.psd`. Still: terminal only, no graphics.
- **Apps**: `open_url` + `std/web.psd` (responsive pages, PWA manifest,
  components) + `examples/mobile_app.psd`; native Android packaging via
  `psd apk` (see next section). Still: iOS needs a Mac + Xcode.
- **Images**: native PPM/BMP engine (`image_info`/`image_process`:
  grayscale/invert/brightness/flip/blur/resize), `examples/image_tool.psd`.
  Still: no JPEG/PNG, no video.

## Android APK packaging (2026-11-07)

`psd apk <app.psd> [--name --package --port --abi --build]` (psd/mobile.py):
- Cross-compiles the PSD program to static-PIE Android executables
  (llvmlite all-targets + zig cc musl; NDK clang fallback): arm64-v8a +
  x86_64, verified ELF/PIE by tests; the x86_64 binary was run on Linux
  and served the example app (200 + UI + PWA manifest).
- Generates a complete Gradle/Android Studio project (manifest with
  extractNativeLibs + INTERNET, MainActivity that ProcessBuilder-starts
  libpsdapp.so from nativeLibraryDir with PSD_PORT and waits for the
  port, legacy jniLibs packaging, app gradle with abiFilters).
- `--build` drives `gradle assembleDebug` when JDK+SDK+Gradle exist;
  otherwise reports exactly what is missing (APK dex/signing is
  Google's toolchain in every language). iOS IPA still requires a
  Mac + Xcode (Apple platform rule), documented in CAPABILITIES.md.
- Integration test: tests/run_tests.py `psd apk android project`.
