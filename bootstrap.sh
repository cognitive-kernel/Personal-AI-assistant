#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
RUNTIME_ROOT="$ROOT/.runtime"
TOOLS_ROOT="$RUNTIME_ROOT/tools"
PYTHON_INSTALL_ROOT="$RUNTIME_ROOT/python"
CACHE_ROOT="$RUNTIME_ROOT/cache"
UV_VERSION="0.13.9"
UV_PATH="$TOOLS_ROOT/uv"

fail() {
    printf '%s\n' "Bootstrap error: $*" >&2
    exit 2
}

detect_target() {
    OS=$(uname -s)
    ARCH=$(uname -m)
    LIBC="gnu"
    if [ "$OS" = "Linux" ]; then
        if (ldd --version 2>&1 || true) | grep -qi musl; then
            LIBC="musl"
        else
            for loader in /lib/ld-musl-*.so.1 /lib64/ld-musl-*.so.1; do
                if [ -e "$loader" ]; then LIBC="musl"; break; fi
            done
        fi
    fi

    case "$OS:$ARCH:$LIBC" in
        Darwin:x86_64:*) TARGET="x86_64-apple-darwin"; SHA256="617a3211261b0fc739562f0071aa34e9739797c510208ede8e2977c30903d041" ;;
        Darwin:arm64:*)  TARGET="aarch64-apple-darwin"; SHA256="5aeca67f2283f75235de40d0bd9c0a5bbc5fce23b6d48b3bf9fa1b2db08382ca" ;;
        Linux:x86_64:gnu) TARGET="x86_64-unknown-linux-gnu"; SHA256="a7fbaff2b9a271d85c1bdb540941090fd1ce011797b4abeb48e4dcd39c2471d5" ;;
        Linux:x86_64:musl) TARGET="x86_64-unknown-linux-musl"; SHA256="58ccf5f1792b8a5a88d01bc910ea131a8b3db4a319a17ad5fad75e0845a3e271" ;;
        Linux:aarch64:gnu) TARGET="aarch64-unknown-linux-gnu"; SHA256="0797542018e541a9288855df555c8077eed06f7a47d206362030480e8ecc9f3d" ;;
        Linux:aarch64:musl) TARGET="aarch64-unknown-linux-musl"; SHA256="37fbb7c5bc66cf99c12aa881949d216181e201dec449772c4329e56487d61179" ;;
        Linux:armv7l:gnu|Linux:armv7:gnu) TARGET="armv7-unknown-linux-gnueabihf"; SHA256="8bd2ce216e6d3fe825ec0fde4532dea708964e8c419844d2ed8e9e7901a7a11e" ;;
        Linux:powerpc64le:gnu) TARGET="powerpc64le-unknown-linux-gnu"; SHA256="5c074a4a008037787c665081116b5b7bc77161d1b5b47ca936732f84b498b317" ;;
        Linux:riscv64:gnu) TARGET="riscv64gc-unknown-linux-gnu"; SHA256="96543eb42d59426db0092f1d860f319abb87c83aaa413a9710108cbd4f68c34b" ;;
        Linux:s390x:gnu) TARGET="s390x-unknown-linux-gnu"; SHA256="7e15c75fbd907334790d60037e492877ed053dff82cc1c0e2a1de0825d0d3864" ;;
        *) fail "Unsupported platform or architecture: OS=$OS ARCH=$ARCH libc=$LIBC. No verified uv $UV_VERSION asset is configured for this target." ;;
    esac
}

test_python_environment() {
    candidate="$1"
    python="$candidate/bin/python"
    [ -x "$python" ] || return 1
    "$python" -c 'import sys, pip; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

install_verified_uv() {
    archive_ext="tar.gz"
    asset="uv-$TARGET.$archive_ext"
    url="https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$asset"
    temp_root="$RUNTIME_ROOT/download-$$"
    archive="$temp_root/$asset"
    extract_root="$temp_root/extract"
    mkdir -p "$extract_root" "$TOOLS_ROOT"
    trap 'rm -rf "$temp_root"' EXIT HUP INT TERM

    if command -v curl >/dev/null 2>&1; then
        curl --fail --location --retry 3 --connect-timeout 20 --max-time 240 --output "$archive" "$url" ||
            fail "Could not download the official uv release asset."
    elif command -v wget >/dev/null 2>&1; then
        wget --tries=3 --timeout=30 --output-document="$archive" "$url" ||
            fail "Could not download the official uv release asset."
    else
        fail "Neither curl nor wget is available for the initial verified download."
    fi

    if command -v sha256sum >/dev/null 2>&1; then
        actual=$(sha256sum "$archive" | awk '{print $1}')
    elif command -v shasum >/dev/null 2>&1; then
        actual=$(shasum -a 256 "$archive" | awk '{print $1}')
    elif command -v openssl >/dev/null 2>&1; then
        actual=$(openssl dgst -sha256 "$archive" | awk '{print $NF}')
    else
        fail "No SHA-256 utility (sha256sum, shasum or openssl) is available."
    fi
    [ "$actual" = "$SHA256" ] || fail "uv archive SHA-256 mismatch; refusing to execute the downloaded file."

    tar -xzf "$archive" -C "$extract_root" || fail "Could not extract the verified uv archive."
    binary=$(find "$extract_root" -type f -name uv -print -quit)
    [ -n "$binary" ] && [ -f "$binary" ] || fail "The verified uv archive did not contain the uv executable."
    cp "$binary" "$UV_PATH.new"
    chmod 755 "$UV_PATH.new"
    mv -f "$UV_PATH.new" "$UV_PATH"
    rm -rf "$temp_root"
    trap - EXIT HUP INT TERM
}

mkdir -p "$RUNTIME_ROOT" "$TOOLS_ROOT" "$PYTHON_INSTALL_ROOT" "$CACHE_ROOT"
export UV_PYTHON_INSTALL_DIR="$PYTHON_INSTALL_ROOT"
export UV_CACHE_DIR="$CACHE_ROOT"
export UV_PYTHON_PREFERENCE="only-managed"
export UV_NO_PROGRESS="1"

detect_target
needs_uv=1
if [ -x "$UV_PATH" ] && [ "$("$UV_PATH" --version 2>/dev/null || true)" = "uv $UV_VERSION" ]; then
    needs_uv=0
fi
if [ "$needs_uv" -eq 1 ]; then
    printf '%s\n' "Installing verified uv $UV_VERSION for $TARGET ..."
    install_verified_uv
fi

VENV_PATH="$ROOT/.venv"
if [ -e "$VENV_PATH" ] && ! test_python_environment "$VENV_PATH"; then
    VENV_PATH="$RUNTIME_ROOT/venvs/python-3.11"
    if [ -e "$VENV_PATH" ] && ! test_python_environment "$VENV_PATH"; then
        fail "The existing project and managed virtual environments are not usable. They were preserved; inspect them before choosing another runtime directory."
    fi
fi

if ! test_python_environment "$VENV_PATH"; then
    if [ -e "$VENV_PATH" ] && [ -n "$(find "$VENV_PATH" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
        fail "The selected virtual-environment directory is non-empty but invalid; refusing to overwrite it."
    fi
    printf '%s\n' "Preparing project-local managed Python 3.11 and virtual environment ..."
    "$UV_PATH" python install 3.11 || fail "Could not install the managed Python 3.11 runtime."
    "$UV_PATH" venv --seed --python 3.11 "$VENV_PATH" || fail "Could not create the project virtual environment."
fi

PYTHON="$VENV_PATH/bin/python"
test_python_environment "$VENV_PATH" || fail "The selected Python environment failed its Python 3.11+ and pip validation."
export PAA_VENV_DIR="$VENV_PATH"

printf '%s\n' "Runtime directory: $RUNTIME_ROOT"
printf '%s\n' "Python environment: $VENV_PATH"
exec "$PYTHON" "$ROOT/run.py" "$@"
