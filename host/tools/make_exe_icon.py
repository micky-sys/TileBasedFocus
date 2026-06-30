#!/usr/bin/env python3
"""Generate ``assets/Iron661_logo.ico`` from ``assets/Iron661_logo.png`` for PyInstaller."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("make_exe_icon: install Pillow (pip install Pillow)", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    host_dir = Path(__file__).resolve().parent.parent
    png_path = host_dir / "assets" / "Iron661_logo.png"
    ico_path = host_dir / "assets" / "Iron661_logo.ico"
    if not png_path.is_file():
        print(f"make_exe_icon: missing {png_path}", file=sys.stderr)
        return 1
    im = Image.open(png_path).convert("RGBA")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    im.save(ico_path, format="ICO", sizes=sizes)
    print(f"make_exe_icon: wrote {ico_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
