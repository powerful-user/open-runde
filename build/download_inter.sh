#!/usr/bin/env bash
#
# Download Inter source fonts from GitHub releases.
# Usage:
#   ./download_inter.sh           # Download latest release
#   ./download_inter.sh v4.1      # Download specific version
#

set -euo pipefail

REPO="rsms/inter"
BUILD_DIR="$(cd "$(dirname "$0")" && pwd)"
INTER_DIR="$BUILD_DIR/inter-source"

# Determine version
if [[ $# -ge 1 ]]; then
    VERSION="$1"
else
    VERSION="$(gh release view --repo "$REPO" --json tagName --jq '.tagName')"
    echo "Latest Inter release: $VERSION"
fi

ZIP_NAME="Inter-${VERSION#v}.zip"
DOWNLOAD_URL="https://github.com/$REPO/releases/download/$VERSION/$ZIP_NAME"

# Download to temp directory
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "Downloading $ZIP_NAME..."
curl -sL "$DOWNLOAD_URL" -o "$TMPDIR/$ZIP_NAME"

echo "Extracting static TTFs..."
# Extract roman and italic static fonts from extras/ttf/
# Exclude InterDisplay- variants (we only use Inter-)
unzip -o "$TMPDIR/$ZIP_NAME" "extras/ttf/Inter-*.ttf" -d "$TMPDIR" | grep -c "inflating" | xargs -I{} echo "  Extracted {} files"

# Clear existing source fonts and copy new ones
rm -f "$INTER_DIR"/*.ttf
mkdir -p "$INTER_DIR"
cp "$TMPDIR"/extras/ttf/Inter-*.ttf "$INTER_DIR/"

echo ""
echo "Installed Inter $VERSION source fonts:"
ls "$INTER_DIR"/*.ttf | while read -r f; do echo "  $(basename "$f")"; done
echo ""
echo "Total: $(ls "$INTER_DIR"/*.ttf | wc -l | tr -d ' ') files"
