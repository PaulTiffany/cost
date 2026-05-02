#!/usr/bin/env python3
"""
verify_certificate.py - Verify the claim_certificate.json self-hash.

Standalone integrity check anyone can run (no dependencies on the
rest of the certificate stack). Reads claim_certificate.json,
recomputes the sha256 over the payload-minus-self-hash, and compares
against the embedded certificate_self_hash field.

Usage
-----
  python ci/verify_certificate.py
  python ci/verify_certificate.py --json /path/to/claim_certificate.json

Exit codes
----------
  0  hash matches — certificate has not been tampered with
  1  hash mismatch — certificate was edited after generation, or the
     stored hash is stale
  2  invocation error (file missing, malformed JSON)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CERT = SCRIPT_DIR / "claim_certificate.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", type=Path, default=DEFAULT_CERT, help=f"path to claim_certificate.json (default: {DEFAULT_CERT})")
    args = parser.parse_args()

    if not args.json.exists():
        print(f"ERROR: certificate not found at {args.json}", file=sys.stderr)
        return 2

    try:
        payload = json.loads(args.json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: malformed JSON: {exc}", file=sys.stderr)
        return 2

    stored_hash = payload.pop("certificate_self_hash", None)
    if stored_hash is None:
        print("ERROR: certificate has no 'certificate_self_hash' field. Either it predates the self-hash feature or was tampered with.", file=sys.stderr)
        return 2

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    computed_hash = hashlib.sha256(canonical).hexdigest()

    print("=" * 70)
    print("CERTIFICATE HASH VERIFICATION")
    print("=" * 70)
    print(f"Certificate:    {args.json}")
    print(f"Stored hash:    {stored_hash}")
    print(f"Computed hash:  {computed_hash}")
    print()

    if stored_hash == computed_hash:
        print("[OK] hash matches — certificate has not been tampered with since generation.")
        return 0
    else:
        print("[FAIL] hash MISMATCH — the certificate JSON was edited after generation,")
        print("       or the embedded hash is stale. Re-run python ci/claim_certificate.py")
        print("       to regenerate; if that doesn't fix it, the JSON has been altered.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
