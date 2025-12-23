#!/bin/bash
set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

REPO="alen-z/time-buddy"
INSTALL_DIR="/usr/local/bin"
BINARY_NAME="time-buddy"

echo "Installing Time Buddy..."

# Check if macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    echo -e "${RED}Error: Time Buddy only works on macOS${NC}"
    exit 1
fi

# Create temp directory
TMP_DIR=$(mktemp -d)
trap "rm -rf $TMP_DIR" EXIT

# Download latest release
echo "Downloading latest release..."
curl -fsSL "https://github.com/${REPO}/releases/latest/download/${BINARY_NAME}" -o "${TMP_DIR}/${BINARY_NAME}"

# Make executable
chmod +x "${TMP_DIR}/${BINARY_NAME}"

# Install to /usr/local/bin
echo "Installing to ${INSTALL_DIR}..."
sudo mv "${TMP_DIR}/${BINARY_NAME}" "${INSTALL_DIR}/${BINARY_NAME}"

echo ""
echo -e "${GREEN}✅ Time Buddy installed successfully!${NC}"
echo ""
echo -e "${YELLOW}⚠️  Required: Enable persistent logging (run once):${NC}"
echo "   sudo log config --subsystem com.apple.loginwindow --mode \"persist:info\""
echo ""
echo "Run 'time-buddy --help' to get started."
