#!/usr/bin/env bash
set -euo pipefail

# Build kloc-cli binary
# Detects platform and builds appropriate binary:
#   - Linux: uses Docker
#   - macOS: builds natively (Docker cannot produce macOS binaries)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

OUTPUT_DIR="$SCRIPT_DIR/dist"
PLATFORM="$(uname -s)"

echo "Building kloc-cli binary for $PLATFORM..."

build_linux() {
    IMAGE_NAME="kloc-cli-builder-linux"
    CONTAINER_NAME="kloc-cli-build-$$"

    if ! command -v docker &> /dev/null; then
        echo "Error: Docker is required. Install from https://docs.docker.com/get-docker/"
        exit 1
    fi

    DOCKERFILE=$(cat <<'DOCKERFILE_EOF'
FROM python:3.12-slim

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends binutils \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml build_entry.py ./
COPY src/ ./src/

RUN uv pip install --system -e . && uv pip install --system pyinstaller

RUN pyinstaller --onefile --name kloc-cli \
    --collect-all src \
    --collect-all rich \
    --clean \
    build_entry.py
DOCKERFILE_EOF
)

    echo "Building in Docker..."
    echo "$DOCKERFILE" | docker build -t "$IMAGE_NAME" -f - .

    docker create --name "$CONTAINER_NAME" "$IMAGE_NAME"
    mkdir -p "$OUTPUT_DIR"
    docker cp "$CONTAINER_NAME:/build/dist/kloc-cli" "$OUTPUT_DIR/kloc-cli"
    docker rm "$CONTAINER_NAME"

    chmod +x "$OUTPUT_DIR/kloc-cli"
    echo "Binary: $OUTPUT_DIR/kloc-cli (Linux)"
}

build_macos() {
    # Docker can't build macOS binaries - must use native PyInstaller
    echo "macOS detected - building natively (Docker cannot produce macOS binaries)"

    if ! command -v python3 &> /dev/null; then
        echo "Error: Python 3 is required for macOS builds."
        echo "Install with: brew install python"
        exit 1
    fi

    # Use uv if available, otherwise pip
    if command -v uv &> /dev/null; then
        echo "Using uv..."
        uv pip install -e . 2>/dev/null || uv pip install --system -e .
        uv pip install pyinstaller 2>/dev/null || uv pip install --system pyinstaller
    else
        echo "Using pip..."
        pip3 install -e .
        pip3 install pyinstaller
    fi

    pyinstaller --onefile --name kloc-cli \
        --collect-all src \
        --collect-all rich \
        --clean \
        build_entry.py

    echo "Binary: $OUTPUT_DIR/kloc-cli (macOS)"
}

# Detect platform and build
case "$PLATFORM" in
    Linux)
        build_linux
        ;;
    Darwin)
        build_macos
        ;;
    *)
        echo "Unsupported platform: $PLATFORM"
        exit 1
        ;;
esac

echo ""
echo "Build complete!"
echo "Test with: ./dist/kloc-cli --help"
