#!/usr/bin/env bash
set -euo pipefail

if ! command -v sudo >/dev/null 2>&1; then
  echo "sudo was not found. Install python3-pip and python3-venv with your distro package manager." >&2
  exit 1
fi

echo "Installing WSL/Linux prerequisites for Config_Converter_to_Ex3400..."
sudo apt update
sudo apt install -y python3 python3-pip python3-venv

echo
echo "Prerequisites installed. Now run:"
echo "  ./run_wsl.sh"
