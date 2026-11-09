#!/bin/bash
# install.sh — install psd-auto on Fedora Linux 45 (works on any modern Fedora/RHEL-like).
#
#   ./install.sh            install for the current user (~/.local)
#   PREFIX=/usr/local ./install.sh   install system-wide (needs write access)
#   ./install.sh --uninstall         remove the user install (keeps your automations and logs)
#   ./install.sh --purge --uninstall remove everything including user specs, logs and AI config
#
# What it does:
#   1. checks python3 >= 3.10, a C compiler and curl (PSD's runtime deps)
#   2. creates the repo venv and installs llvmlite (build-time only)
#   3. AOT-compiles src/main.psd to a native binary (psd-language toolchain)
#   4. copies the toolchain-free runtime into $PREFIX/share/psd-auto
#   5. writes $PREFIX/bin/psd-auto (thin launcher) and a systemd user unit for the dashboard
set -euo pipefail

UNINSTALL=0
PURGE=0
for arg in "$@"; do
    case "$arg" in
        --uninstall) UNINSTALL=1 ;;
        --purge) PURGE=1 ;;
        -h|--help) grep '^#' "$0" | head -20; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

HERE="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$HERE/.." && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
SHARE="$PREFIX/share/psd-auto"
BIN_DIR="$PREFIX/bin"
WRAPPER="$BIN_DIR/psd-auto"

if [ "$UNINSTALL" = "1" ]; then
    echo "removing $WRAPPER and $SHARE ..."
    rm -f "$WRAPPER"
    rm -rf "$SHARE"
    rm -f "$HOME/.config/systemd/user/psd-auto-web.service"
    if [ "$PURGE" = "1" ]; then
        echo "purging user specs, state and config ..."
        rm -rf "$HOME/.config/psd-auto" "$HOME/.local/state/psd-auto"
    else
        echo "kept your automations (~/.config/psd-auto) and logs (~/.local/state/psd-auto). Use --purge to remove them too."
    fi
    systemctl --user daemon-reload 2>/dev/null || true
    echo "uninstalled."
    exit 0
fi

echo "== psd-auto installer =="
echo "install prefix: $PREFIX"

# --- 1. dependencies -------------------------------------------------------
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing dependency: $1${2:+ — $2}" >&2; exit 1; }; }
need python3 "Fedora: sudo dnf install python3"
PYV=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || {
    echo "python3 >= 3.10 required (found $PYV)" >&2; exit 1; }
need cc "Fedora: sudo dnf install gcc"
need curl "Fedora: sudo dnf install curl"
echo "deps ok (python $PYV, cc, curl)"

# --- 2. venv + llvmlite ----------------------------------------------------
VENV="$REPO_ROOT/.venv"
if [ ! -x "$VENV/bin/python" ]; then
    echo "creating venv $VENV ..."
    python3 -m venv "$VENV"
fi
if ! "$VENV/bin/python" -c 'import llvmlite' >/dev/null 2>&1; then
    echo "installing llvmlite ..."
    "$VENV/bin/pip" install --quiet llvmlite
fi

# --- 3. build the native binary --------------------------------------------
BUILD_DIR="$HERE/build"
mkdir -p "$BUILD_DIR"
echo "compiling psd-auto (AOT, via psd-language) ..."
# build to a temp file then rename(2) into place: replacing a running
# executable with rename works, while overwriting it in-place fails (ETXTBSY)
PYTHONPATH="$REPO_ROOT/psd-language" "$VENV/bin/python" -m psd build "$HERE/src/main.psd" -o "$BUILD_DIR/psd-auto.tmp"
mv -f "$BUILD_DIR/psd-auto.tmp" "$BUILD_DIR/psd-auto"

# --- 4. install the runtime -------------------------------------------------
echo "installing into $SHARE ..."
mkdir -p "$SHARE/src" "$SHARE/automations" "$SHARE/build" "$BIN_DIR"
cp "$HERE"/src/*.psd "$SHARE/src/"
cp "$HERE"/automations/*.json "$SHARE/automations/"
cp "$BUILD_DIR/psd-auto" "$SHARE/build/psd-auto"
chmod 755 "$SHARE/build/psd-auto"

# --- 5. launcher + systemd unit ---------------------------------------------
cat > "$WRAPPER" <<EOF
#!/bin/sh
export PSD_AUTO_ROOT="$SHARE"
export PSD_AUTO_BIN="$WRAPPER"
exec "$SHARE/build/psd-auto" "\$@"
EOF
chmod 755 "$WRAPPER"

mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/psd-auto-web.service" <<EOF
[Unit]
Description=psd-auto dashboard (Fedora 45 workstation autopilot)
After=network.target

[Service]
Type=simple
ExecStart=$WRAPPER web --port 8099
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

echo ""
if ! printf '%s' ":$PATH:" | grep -q ":$BIN_DIR:"; then
    echo "note: $BIN_DIR is not on your PATH. Add it:"
    echo "  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.bashrc && source ~/.bashrc"
    echo ""
fi

"$WRAPPER" init
echo ""
echo "installed. try:"
echo "  psd-auto list"
echo "  psd-auto doctor"
echo "  psd-auto web        # dashboard on http://127.0.0.1:8099/"
echo "  systemctl --user enable --now psd-auto-web   # dashboard at login (optional)"
