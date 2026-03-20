"""
scripts/copy_edge_profile.py
─────────────────────────────
Copies cookies, history and local storage from your real Edge profile
into the Playwright persistent profile used by ao_fetch.py.

IMPORTANT: Close all Edge windows before running this.

Cookies on Windows are encrypted with DPAPI (tied to the current Windows
user), so copying them to another directory on the same machine works fine.
"""

import shutil
import sys
from pathlib import Path

REAL_PROFILE  = Path(r"C:\Users\venka\AppData\Local\Microsoft\Edge\User Data\Default")
AO_PROFILE    = Path.home() / ".angelone" / "edge_profile" / "Default"

# Files and folders to copy across
ITEMS = [
    "Cookies",
    "History",
    "Favicons",
    "Web Data",
    "Local Storage",
    "Session Storage",
    "Extension Cookies",
]


def main() -> None:
    if not REAL_PROFILE.exists():
        print(f"[copy] Real Edge profile not found: {REAL_PROFILE}")
        sys.exit(1)

    AO_PROFILE.mkdir(parents=True, exist_ok=True)
    print(f"[copy] Source : {REAL_PROFILE}")
    print(f"[copy] Target : {AO_PROFILE}")
    print()

    for item in ITEMS:
        src = REAL_PROFILE / item
        dst = AO_PROFILE   / item

        if not src.exists():
            print(f"  skip  {item}  (not found in real profile)")
            continue

        try:
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            print(f"  ok    {item}")
        except PermissionError:
            print(f"  FAIL  {item}  — Edge may still be running (file locked)")
        except Exception as exc:
            print(f"  FAIL  {item}  — {exc}")

    print()
    print("[copy] Done. Run ao_fetch.py — it will start with your real cookies and history.")


if __name__ == "__main__":
    main()
