"""Set/replace the console owner (terminal-only). Usage:
  python scripts/set_owner.py --email you@mail.com   (prompts hidden password)
  X5_EMAIL=.. X5_PASSWORD=.. python scripts/set_owner.py   (non-interactive)
Overwrites X5Agent/Data/database/console_auth.json with a salted hash only.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from core.paths import data_db_file  # noqa: E402

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def main() -> int:
    email = os.environ.get("X5_EMAIL", "").strip().lower()
    password = os.environ.get("X5_PASSWORD", "")
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--email" and i + 1 < len(args):
            email = args[i + 1].strip().lower()
    if not email:
        try:
            email = input("  email: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return 1
    if not EMAIL_RE.match(email):
        print("  ERROR: invalid email format", file=sys.stderr)
        return 1
    if not password:
        try:
            password = getpass.getpass("  password (min 8, hidden): ")
        except (EOFError, KeyboardInterrupt):
            return 1
    if len(password) < 8:
        print("  ERROR: password too short (min 8)", file=sys.stderr)
        return 1
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), 200_000).hex()
    del password
    path = data_db_file("console_auth.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"email": email, "salt": salt, "hash": digest,
                                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                             time.gmtime())},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Owner saved ({email}) - hash only, never plaintext")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
