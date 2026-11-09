# What PSD can build now — honestly

This release closes the five gaps that used to be "not PSD" territory.
Each area says what works, the PSD way to build it, how it compares
with Python, and where the honest boundary still is. The rule for this
project: advantages are only claimed where they are real (single
compiled binary, native speed for compiled loops, zero dependencies),
and limits are stated plainly.

## 1. Mobile / desktop "apps"

**Local app (desktop):** one binary serves a responsive web UI and
opens it: `http_listen(...)` + `open_url(url)` + `std/web.psd`
(`page`, `base_css` with mobile layout + 44px touch targets,
components, `pwa_manifest`). Example: `examples/mobile_app.psd`.
No server to install, no interpreter — the binary *is* the app.

**Real Android APK:** `psd apk examples/mobile_app.psd --name "PSD
Notes" --package com.psd.notes` generates a complete Android project
and cross-compiles your PSD program to a static-PIE ARM64/x86_64
executable (via `zig cc`, no NDK needed; NDK clang also supported).
The app runs that binary on the phone and shows its local UI in a
WebView — your PSD logic, on-device, offline. Open the project in
Android Studio (Build > Build APK) or `psd apk ... --build` with
JDK+SDK+Gradle installed to assemble/sign the APK. The generated
x86_64 binary is directly testable on Linux; the ARM64 binary is
verified as a proper static-PIE ELF by the test suite.

**vs Python:** Python mobile means Kivy/Buildozer (heavy, fragile)
or a web stack + wrapper. PSD compiles the *same program you ran on
desktop* into the phone binary — one language, one codebase.

**Honest boundary:** APK assembly/signing uses Google's toolchain
(Gradle/SDK) in every language; PSD generates everything and drives
it when installed. Play Store release signing needs your keystore
(standard). iOS IPA builds require a Mac with Xcode — Apple's rule
for Flutter/React Native too — so PSD targets Android natively today.

## 2. Games

**PSD way:** terminal games are first-class: `term_clear/term_home`,
`read_key()` (arrows/WASD-normalized single keys, non-echo raw mode),
`term_size()`, `sleep_ms/time_ms/random/rand_int`, and `std/game.psd`
(grid, box drawing, points, key directions, seeded RNG). Example:
`examples/game_snake.psd` (playable Snake). Turn-based and grid games
— snake, 2048-likes, roguelikes, tic-tac-toe, text adventures — are
very buildable, at native speed with zero dependencies.

**vs Python:** the same games in Python need curses (Unix-only,
fiddly) or pygame (a big dependency). PSD needs nothing installed.

**Honest boundary:** no graphics engine, sprites, or audio — real-time
3D/2D games belong in Godot/Unity/Love2D. A browser-canvas game served
by PSD is possible (serve JS from your binary), but the game code is
then JavaScript, not PSD.

## 3. AI / ML training

**PSD way:** `std/numeric.psd` (matrices: matmul/transpose/matvec/
activations) + `std/nn.psd` (MLP with real backpropagation training,
seeded init, JSON model save/load) + native `exp/log/pow`. Example:
`examples/nn_xor.psd` trains a 2-4-1 network that learns XOR and
persists the model. Tabular regression stays in `std/ml.psd`.

Measured on this repo's benchmark (identical algorithm/epochs,
`bench/nn_bench.psd` vs `bench/nn_compare.py`): PSD ~357 ms vs pure
Python ~749 ms — about 2×, because the same loops are compiled.
Python with NumPy/PyTorch vectorization beats both; that is exactly
the boundary:

**Honest boundary:** no tensors/GPU/autodiff/transformers. Train
small MLPs and classical models natively; for deep learning, either
call Python as a subprocess (`run([...])`, the interop pattern) or
use PyTorch directly. PSD's win is dependency-free small-model
training *inside* your shipped binary.

## 4. High-traffic websites

**PSD way:** `http_listen_pool(port, host, workers)` preforks N
worker processes on one listening socket (OS load-balances accepts);
each worker runs the normal accept loop. Plus `http_reply_file`
(streamed static files with Content-Length), `http_redirect`, and
request `headers` in the request map. Example:
`examples/server_pool.psd`. One binary, no framework, no interpreter
per request; workers share state via files/json like any process pool
(and a `PSD_SERVE_COUNT` budget makes pools testable/CI-friendly).

**vs Python:** Python's stdlib server is single-threaded; real
deployments need gunicorn/uvicorn + a framework + an interpreter.
PSD compiles the whole thing to one self-contained binary.

**Honest boundary:** HTTP/1.1 only, no TLS (terminate HTTPS with
Caddy/nginx in front — standard practice), no WebSockets yet, no
async I/O within a worker. A compiled prefork pool handles serious
small/medium traffic; it is not a CDN or a clustered platform.

## 5. Image / media tools

**PSD way:** a native image engine in the runtime: `image_info(path)`
and `image_process(in, out, op, arg)` for PPM (P3/P6) and BMP 24-bit
— grayscale, invert, brightness, flip, 3x3 blur, resize — with
structured json results (`{ok,width,height,format}` or an error).
Example: `examples/image_tool.psd` (CLI batch converter/filter).

**vs Python:** PIL/Pillow is an external dependency (and a native
build); PSD does these basics with zero installs, in the same binary.

**Honest boundary:** no JPEG/PNG decode yet, no layers/text/effects
stack, and no video at all (FFmpeg's domain — call it via `run()`).
This is a converter/filter engine, not Photoshop or a video editor.

## Summary table

| Area | PSD today | Python today | Honest PSD limit |
|---|---|---|---|
| CLI/automation/agents | native binary, std libs | interpreter + deps | — |
| Mobile/desktop app | local web app + PWA, auto-open | GUI toolkit or web stack | no native store app |
| Games | terminal/grid games, zero-dep | curses/pygame deps | no graphics engine |
| AI training | small MLPs + tabular, ~2× pure Python | NumPy/PyTorch ecosystem | no GPU/deep learning |
| Websites | prefork pool, streamed files | framework + app server | no TLS/WebSockets |
| Images | PPM/BMP filters/convert | Pillow dependency | no JPEG/PNG/video |
