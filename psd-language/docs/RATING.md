# PSD Rating — AI training, tool-use, GUI apps, automation (v0.4, 2026-11-07)

Scores are out of 10, judged on *what a user can actually ship today*,
with the evidence in this repo. "Before" is commit `ff381ab` (v0.3);
"After" is this patch. Anything not backed by a test or a runnable
example is scored as absent.

## Summary

| Axis | Before | After | Verdict |
|---|---|---|---|
| 1. AI model development & training (native) | 3 | 5 | Real small-model training now; still not a tensor framework |
| 2. Giving models tools + training/fine-tuning them (orchestration) | 5 | 7.5 | Tool-calling loop + JSONL datasets are first-class stdlib |
| 3. Building apps with a proper GUI | 4 | 6.5 | Component-based server UI + redirects/headers; browser-only |
| 4. Automation | 7.5 | 8.5 | Strongest axis; timing/dirs/stdin/recoverable runs filled in |
| 5. AI-writability (models writing PSD) | 7 | 8 → **9 (v0.5)** | Toolchain answers its own questions; warm loop ~0.09 s |

## 1. AI model development & training — 3 → 5

**Before.** Only scalar least squares (`std/ml.fit` on `[float]`),
documented honestly as "not a tensor library". Fatal gaps for any real
training task: no `exp`/`log`/`pow` (no sigmoid, softmax, or
cross-entropy), no multivariate models, no classifier, no metrics beyond
MSE, no feature scaling, no dataset format, no train/test split.

**After (all in `psd/std/ml.psd`, tested in `tests/cases/ml_multi.psd`).**
`fit_multi` (multivariate linear regression, gradient descent),
`fit_logistic` + `predict_proba/predict_label/accuracy` (binary
classifier), `sigmoid`, `rmse`, `r2`, `fit_scaler/apply_scaler`,
`train_test_split`, plus the float-math builtins `exp/log/pow` they are
built on. `std/data.psd` loads numeric CSV (`csv_numbers/load_numbers`,
`to_matrix/column/features`). End-to-end: `examples/train_classifier.psd`
trains from CSV, evaluates at 1.0 accuracy, saves the model as json.

**Still weak (not hidden).** No tensors, matmul, autograd, GPU, epochs
over large data, or framework interop beyond json files. Anything beyond
small tabular models must be delegated to an external trainer over
`run([...])` + json (`examples/ml_interop.psd`) — that seam is good, the
native ceiling is low. Score capped at 5 until there is a real numeric
array type.

## 2. Giving models tools + train/fine-tune — 5 → 7.5

**Before.** The raw seams existed — `json`, `http_request`, injection-safe
`run/capture`, `try_*` envelopes — but every agent had to hand-build chat
payloads, tool schemas, and tool-call parsing, and there was no dataset
format for fine-tuning. Models also had to parse human compiler text.

**After (`psd/std/agent.psd`, `tests/cases/agentx.psd`).**
`tool_spec/string_params` build function-calling schemas,
`chat_payload/chat` target OpenAI-compatible endpoints,
`reply_content/tool_calls/wants_tools` parse replies (malformed argument
json becomes `{"_raw": ...}` data, never a crash), and
`tool_result` closes the loop. Dispatch is an explicit name→if/elif
chain — PSD has no function values, which makes the tool surface
auditable by construction. Fine-tuning datasets are stdlib too:
`chat_example/instruction_example` + `save_jsonl/load_jsonl` (JSONL).
And `psd check --json` emits `{"ok", "errors": [{file, line, col,
message}]}` so an agent (or CI) can fix code without scraping text.

**Still weak.** No streaming responses, no token counting, no provider
SDKs, no eval harness, and actually *running* a fine-tune still means
calling a provider API or local trainer yourself. The plumbing is done;
the platform is yours to plug in.

## 3. Apps with a proper GUI — 4 → 6.5

**Before.** A sequential HTTP server existed, but UIs were raw string
concatenation: requests hid headers (no cookies/auth/Content-Type),
there was no redirect (every form flow hacked around it), and forms were
hand-split on `&`/`=` in each app. It worked; it did not feel like a GUI
toolkit.

**After (`psd/std/web.psd`, `tests/cases/agentx.psd`, server test).**
Components that escape by default (`page/h1/p/link/nav/list_items/
table`, `form_open/text_input/submit_button`), `parse_form/form_value`
stdlib form+query parsing, `reply_html/reply_json/reply_page/not_found`,
and real HTTP mechanics underneath: `http_redirect()` (302) and
`req["headers"]` (lowercased) in the runtime.
`examples/gui_dashboard.psd` is a dashboard with add-task form →
redirect → JSON API, verified live with curl. The 8-route server
integration test covers routing, escaping, forms, json, 404, redirect,
and headers.

**Still weak (stated plainly).** Browser is the only GUI — no desktop or
mobile widgets; the server is sequential HTTP/1.1 with no TLS,
WebSockets, sessions, or static-file helper, so anything public needs a
reverse proxy and hand-rolled cookies (now at least *readable* via
headers). 6.5 means "pleasant for local tools and dashboards", not
"ship a SaaS frontend".

## 4. Automation — 7.5 → 8.5

**Before.** Already the strongest axis: argv-safe processes, explicit
shell, `try_*` envelopes with timeouts, files, env/args, HTTP, capability
switches. Everyday scripts still hit papercuts: no sleep or clock (no
retries/backoff/polling), no mkdir, no stdin (no interactive tools), and
`run()` failures were process-fatal (no recoverable variant).

**After (`tests/cases/mathx.psd`, `examples/automation_pro.psd`).**
`sleep_ms`, `time_ms`, `random/rand_int` (backoff, jitter, sampling),
`make_dir/try_make_dir` (idempotent), `read_line([prompt])` for
interactive CLIs, `cwd()`, `try_run(argv[, timeout]) -> {ok, code}`, and
`try_float` for forgiving data ingestion. The retry-with-backoff pattern
is now a 15-line example instead of an impossibility.

**Still weak.** No glob/recursive walk, no regex, no parallel process
pool, no cron/daemon helpers, and HTTP still shells out to `curl`
(120 s cap, no streaming). Very solid for scripts and pipelines; not yet
an orchestration platform.

## 5. AI-writability — 7 → 8

Small unambiguous grammar, `AI_GUIDE.md`, machine-validated doc
snippets, and did-you-mean errors were already there. `--json`
diagnostics, plus stdlib names that mirror the concepts an LLM already
knows (`fit_logistic`, `chat_payload`, `parse_form`), close the
generate→check→fix loop.

**v0.5 (this patch) — faster + easier, measured.** The loop an AI runs
hundreds of times was dominated by recompiling the C runtime + LLVM on
every `psd run` (~0.99 s). A content-hash build cache (every source file
+ the whole toolchain in the key; edited code always recompiles — there
is a no-stale-binaries test) makes warm runs **0.089 s (~11×)**; `check`
was already 0.06 s. Understanding got cheaper too: `psd doc --json`
serves a catalog of all 54 builtins + 76 stdlib functions that the test
suite asserts is in sync with the checker and the std sources, `psd
guide` prints a one-screen reference with a compiling skeleton, `psd
new --template …` scaffolds five project shapes that type-check
immediately, `psd fmt` makes layout canonical (the repo is now
fmt-clean), `check --json` adds a per-error `hint` + source `snippet`,
and the classic Python-isms an LLM writes (`'single quotes'`, `None`,
`list[int]`, `input()`) now fail with the PSD spelling in the message.
Tests: 63 passing, including catalog/fmt/scaffold/cache checks. Still
missing: machine-readable *runtime* errors and editor/LSP support.

## What this patch changed

- **Runtime (`psd/runtime.c`)**: `exp/log/pow`, `sleep_ms/time_ms`,
  `random/rand_int`, `read_line`, `cwd`, `make_dir`, `try_float`,
  `try_make_dir`, `try_run`, `http_redirect`, request `headers`.
- **Checker/codegen**: all of the above as typed builtins.
- **Stdlib**: `std/ml.psd` (multivariate + logistic + metrics + scaler),
  new `std/data.psd`, `std/agent.psd`, `std/web.psd`.
- **CLI**: `psd check --json` structured diagnostics.
- **Examples**: `train_classifier`, `agent_tools`, `gui_dashboard`,
  `automation_pro`. **Tests**: 55 passing (52 before + `mathx`,
  `ml_multi`, `agentx`), ASan-clean, server test now 8 routes.

## Next gaps, in order

1. Numeric array type + matmul (raises Axis 1's ceiling, not its floor).
2. Streaming HTTP + WebSockets, sessions/cookies helpers (Axis 3).
3. `psd fmt`, glob/walk, regex-lite (Axes 4-5).
