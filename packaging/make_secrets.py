#!/usr/bin/env python3
"""
Generate the build-time secrets module.

    python packaging/make_secrets.py            # create one if absent
    python packaging/make_secrets.py --force    # rotate to a new key

Writes ``utils/_secrets.py``, which is **gitignored**. That file holds the HMAC
key protecting the local licence state file.

Why generate rather than hardcode: this repository has a public remote, and a
secret committed even once remains in git history forever, surviving any later
edit. Keeping the real key in an untracked, generated file means it can be baked
into a frozen build without ever being committed.

Run this once on your build machine and keep a copy of the file somewhere safe
(a password manager is fine). **If you lose it and generate a new key, every
existing installation's stored licence state stops validating** — the app treats
an unverifiable state file as "no licence" and falls back to trial, so nobody is
locked out, but activated customers would have to re-enter their key.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "utils" / "_secrets.py"

TEMPLATE = '''"""
Build-time secrets. GENERATED FILE -- do not commit, do not edit by hand.

Created by packaging/make_secrets.py. This file is listed in .gitignore because
the repository has a public remote and a committed secret cannot be removed from
git history.

Keep a backup: losing this key means existing installations' stored licence
state can no longer be verified, so activated customers would have to re-enter
their licence key (they are not locked out -- unverifiable state falls back to
trial).
"""

STATE_SIGNING_KEY = "{key}"
'''


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate utils/_secrets.py")
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite an existing key (rotates it — read the warning above)",
    )
    args = parser.parse_args()

    if TARGET.exists() and not args.force:
        print(f"{TARGET} already exists — leaving it alone.")
        print("Use --force to rotate the key (this invalidates stored licence state).")
        return 0

    if TARGET.exists() and args.force:
        print("WARNING: rotating the signing key.")
        print("Existing installations will fall back to trial and customers will")
        print("need to re-enter their licence keys. Continue? [y/N] ", end="")
        if input().strip().lower() != "y":
            print("Aborted.")
            return 1

    key = secrets.token_urlsafe(48)
    TARGET.write_text(TEMPLATE.format(key=key), encoding="utf-8")
    print(f"Wrote {TARGET}")
    print("This file is gitignored. Back it up somewhere safe (e.g. a password manager).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
