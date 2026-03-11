#!/usr/bin/env bash
# ClawSmith installer for macOS / Linux
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Cool-Coder174/ClawSmith/main/install.sh | bash
#   or: bash install.sh
set -euo pipefail

REPO="https://github.com/Cool-Coder174/ClawSmith.git"
MIN_PYTHON="3.11"
INSTALL_DIR="${CLAWSMITH_DIR:-$HOME/ClawSmith}"

# -- helpers ---------------------------------------------------------------

info()  { printf "\033[0;36m[clawsmith]\033[0m %s\n" "$*"; }
ok()    { printf "\033[0;32m[clawsmith]\033[0m %s\n" "$*"; }
warn()  { printf "\033[0;33m[clawsmith]\033[0m %s\n" "$*"; }
fail()  { printf "\033[0;31m[clawsmith]\033[0m %s\n" "$*"; exit 1; }

# -- detect python ---------------------------------------------------------

find_python() {
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || true)
            if [ -n "$ver" ]; then
                local major minor
                major=${ver%%.*}
                minor=${ver#*.}
                if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
                    PYTHON="$cmd"
                    PYTHON_VER="$ver"
                    return 0
                fi
            fi
        fi
    done
    return 1
}

# -- main ------------------------------------------------------------------

info "ClawSmith installer"
echo ""

if ! find_python; then
    fail "Python $MIN_PYTHON+ is required but not found. Install it from https://python.org"
fi
ok "Python $PYTHON_VER found ($PYTHON)"

if ! command -v git &>/dev/null; then
    fail "git is required but not found."
fi
ok "git found"

# Clone or update
if [ -d "$INSTALL_DIR/.git" ]; then
    info "Updating existing install at $INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only || warn "git pull failed; continuing with existing code"
else
    info "Cloning ClawSmith to $INSTALL_DIR"
    git clone "$REPO" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# Prefer pipx for isolated install, fall back to pip
if command -v pipx &>/dev/null; then
    info "Installing via pipx..."
    pipx install --editable . --force 2>/dev/null || pipx install -e . --force
    ok "Installed via pipx"
elif command -v pip3 &>/dev/null || command -v pip &>/dev/null; then
    PIP="${PYTHON} -m pip"
    info "Installing via pip (editable)..."
    $PIP install -e ".[dev]" --quiet
    ok "Installed via pip"
else
    fail "Neither pipx nor pip found. Install one of them first."
fi

# Ensure the scripts directory is on PATH
if ! command -v clawsmith &>/dev/null; then
    SCRIPTS_DIR=$("$PYTHON" -c "import sysconfig; print(sysconfig.get_path('scripts', 'posix_user'))" 2>/dev/null)
    if [ -z "$SCRIPTS_DIR" ]; then
        USER_BASE=$("$PYTHON" -m site --user-base 2>/dev/null)
        [ -n "$USER_BASE" ] && SCRIPTS_DIR="$USER_BASE/bin"
    fi

    if [ -n "$SCRIPTS_DIR" ] && [ -x "$SCRIPTS_DIR/clawsmith" ]; then
        export PATH="$PATH:$SCRIPTS_DIR"

        SHELL_NAME=$(basename "$SHELL" 2>/dev/null)
        case "$SHELL_NAME" in
            zsh)  RC_FILE="$HOME/.zshrc" ;;
            bash) RC_FILE="$HOME/.bashrc" ;;
            fish) RC_FILE="$HOME/.config/fish/config.fish" ;;
            *)    RC_FILE="" ;;
        esac

        if [ -n "$RC_FILE" ]; then
            if ! grep -qF "$SCRIPTS_DIR" "$RC_FILE" 2>/dev/null; then
                info "Adding $SCRIPTS_DIR to PATH in $RC_FILE"
                if [ "$SHELL_NAME" = "fish" ]; then
                    echo "fish_add_path $SCRIPTS_DIR" >> "$RC_FILE"
                else
                    echo "export PATH=\"\$PATH:$SCRIPTS_DIR\"" >> "$RC_FILE"
                fi
            fi
        fi
        ok "clawsmith CLI added to PATH"
    else
        warn "clawsmith is installed but could not locate the scripts directory."
        warn "You may need to add ~/.local/bin to your PATH."
    fi
else
    ok "clawsmith CLI is on PATH"
fi

echo ""
ok "Installation complete!"
echo ""
info "Next steps:"
echo "  cd $INSTALL_DIR"
echo "  clawsmith onboard       # guided first-run setup"
echo "  clawsmith doctor        # verify environment"
echo "  clawsmith smoke-test    # quick integration check"
echo ""
