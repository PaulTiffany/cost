#!/usr/bin/env python3
"""
pdf_camera_ready_check.py - Inspect paper/main.pdf for two camera-ready
properties.

  (a) Author identifier scrub. PDF metadata (Author, Creator, Producer,
      Title, Subject, Keywords) must not contain user identifiers such as
      "<redacted_user>", "Paul Tiffany", or "<redacted_user>tiffany". A match is a blocker.

  (b) Font subsetting. Embedded font names that begin with a six-character
      uppercase prefix followed by '+' are considered subset (LaTeX's
      conventional encoding). The check warns if more than 20 percent of
      embedded fonts are not subset. Advisory only.

If pypdf is installed, metadata and font names are read through it. If it
is not, the script falls back to scanning the binary tail with small
regular expressions.

Exit codes
----------
  0  no metadata leak (font subsetting is advisory)
  1  metadata leak detected
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_PDF = REPO_ROOT / "paper" / "main.pdf"
RESULTS_JSON = SCRIPT_DIR / "pdf_camera_ready_results.json"

AUTHOR_IDENTIFIER_PATTERNS = [
    re.compile(r"\b<redacted_user>\b", re.IGNORECASE),
    re.compile(r"<redacted_user>tiffany", re.IGNORECASE),
    re.compile(r"Paul\s+Tiffany", re.IGNORECASE),
    re.compile(r"Paul\s+C\.?\s+Tiffany", re.IGNORECASE),
    re.compile(r"\bTiffany\b", re.IGNORECASE),
]

METADATA_FIELDS = ("Author", "Creator", "Producer", "Title", "Subject", "Keywords")

# Subset prefix as written by LaTeX engines: AAAAAA+RealFontName.
SUBSET_PREFIX_RE = re.compile(r"^[A-Z]{6}\+")
FONT_SUBSET_THRESHOLD = 0.20  # warn if more than 20 percent are full fonts


def _read_metadata_pypdf(pdf_path: Path) -> dict[str, str]:
    """Try to load metadata via pypdf. Returns an empty dict on failure."""
    try:
        import pypdf  # type: ignore
    except ImportError:
        return {}
    try:
        reader = pypdf.PdfReader(str(pdf_path))
        meta = reader.metadata or {}
    except Exception:
        return {}
    out: dict[str, str] = {}
    for key in METADATA_FIELDS:
        slash_key = "/" + key
        value = meta.get(slash_key) if hasattr(meta, "get") else None
        if value is None:
            continue
        try:
            out[key] = str(value)
        except Exception:
            continue
    return out


def _read_metadata_fallback(pdf_path: Path) -> dict[str, str]:
    """Parse metadata directly from the PDF byte stream.

    Reads the last 64 KiB and pulls /Field (string) entries. The PDF info
    dictionary lives near EOF in linearized files; this is a best-effort
    parser, not a full PDF parser.
    """
    try:
        size = pdf_path.stat().st_size
    except OSError:
        return {}
    tail_size = min(size, 65536)
    try:
        with pdf_path.open("rb") as fh:
            fh.seek(size - tail_size)
            tail = fh.read(tail_size)
    except OSError:
        return {}

    text = tail.decode("latin-1", errors="replace")
    out: dict[str, str] = {}
    for key in METADATA_FIELDS:
        # Match /Author (some text) or /Author <hexstring>.
        m = re.search(rf"/{key}\s*\(([^)]*)\)", text)
        if m:
            out[key] = m.group(1)
            continue
        m = re.search(rf"/{key}\s*<([0-9A-Fa-f\s]+)>", text)
        if m:
            hex_clean = re.sub(r"\s+", "", m.group(1))
            try:
                # PDF hex strings can be UTF-16BE with BOM.
                raw = bytes.fromhex(hex_clean)
                if raw.startswith(b"\xfe\xff"):
                    out[key] = raw[2:].decode("utf-16-be", errors="replace")
                else:
                    out[key] = raw.decode("latin-1", errors="replace")
            except ValueError:
                continue
    return out


def _read_font_names_pypdf(pdf_path: Path) -> list[str]:
    """Pull /BaseFont names from each page via pypdf, if available."""
    try:
        import pypdf  # type: ignore
    except ImportError:
        return []
    try:
        reader = pypdf.PdfReader(str(pdf_path))
    except Exception:
        return []
    names: set[str] = set()
    for page in reader.pages:
        try:
            resources = page.get("/Resources")
            if resources is None:
                continue
            try:
                resources = resources.get_object()
            except Exception:
                pass
            fonts = resources.get("/Font") if hasattr(resources, "get") else None
            if fonts is None:
                continue
            try:
                fonts = fonts.get_object()
            except Exception:
                pass
            for _key, font_ref in (fonts.items() if hasattr(fonts, "items") else []):
                try:
                    font_obj = font_ref.get_object()
                except Exception:
                    font_obj = font_ref
                base = font_obj.get("/BaseFont") if hasattr(font_obj, "get") else None
                if base:
                    names.add(str(base).lstrip("/"))
        except Exception:
            continue
    return sorted(names)


def _read_font_names_fallback(pdf_path: Path) -> list[str]:
    """Scan the entire PDF for /BaseFont /Name entries."""
    try:
        data = pdf_path.read_bytes()
    except OSError:
        return []
    text = data.decode("latin-1", errors="replace")
    matches = re.findall(r"/BaseFont\s*/([A-Za-z0-9+\-_.]+)", text)
    return sorted(set(matches))


def _scan_metadata_for_identifiers(metadata: dict[str, str]) -> list[dict]:
    findings = []
    for field, value in metadata.items():
        if not value:
            continue
        for pat in AUTHOR_IDENTIFIER_PATTERNS:
            if pat.search(value):
                findings.append({
                    "field": field,
                    "value": value,
                    "pattern": pat.pattern,
                })
                break
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdf", default=str(DEFAULT_PDF),
                        help="path to PDF (default: paper/main.pdf)")
    parser.add_argument("--json-out", default=str(RESULTS_JSON),
                        help="path for JSON results")
    args = parser.parse_args(argv)

    pdf_path = Path(args.pdf)
    out_path = Path(args.json_out)

    if not pdf_path.exists():
        print(f"PDF not found at {pdf_path}; skipping")
        out_path.write_text(json.dumps({"skipped": True, "pdf_path": str(pdf_path)}, indent=2),
                            encoding="utf-8")
        return 0

    try:
        import pypdf  # noqa: F401
        used_pypdf = True
    except ImportError:
        used_pypdf = False

    if used_pypdf:
        metadata = _read_metadata_pypdf(pdf_path)
        font_names = _read_font_names_pypdf(pdf_path)
        if not metadata:
            metadata = _read_metadata_fallback(pdf_path)
        if not font_names:
            font_names = _read_font_names_fallback(pdf_path)
    else:
        metadata = _read_metadata_fallback(pdf_path)
        font_names = _read_font_names_fallback(pdf_path)

    leaks = _scan_metadata_for_identifiers(metadata)

    n_fonts = len(font_names)
    subset = [n for n in font_names if SUBSET_PREFIX_RE.match(n)]
    not_subset = [n for n in font_names if not SUBSET_PREFIX_RE.match(n)]
    if n_fonts:
        not_subset_ratio = len(not_subset) / n_fonts
    else:
        not_subset_ratio = 0.0
    font_warning = n_fonts > 0 and not_subset_ratio > FONT_SUBSET_THRESHOLD

    payload = {
        "pdf_path": str(pdf_path),
        "used_pypdf": used_pypdf,
        "metadata": metadata,
        "metadata_leaks": leaks,
        "fonts": {
            "total": n_fonts,
            "subset": len(subset),
            "not_subset": len(not_subset),
            "not_subset_ratio": round(not_subset_ratio, 3),
            "threshold": FONT_SUBSET_THRESHOLD,
            "warning": font_warning,
            "names": font_names,
        },
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    status = "PASS" if not leaks else "FAIL"
    print(f"[pdf_camera_ready] {status}")
    print(f"  pdf            : {pdf_path}")
    print(f"  pypdf used     : {used_pypdf}")
    print(f"  metadata fields: {len(metadata)}")
    if metadata:
        for k, v in metadata.items():
            short = v if len(v) <= 80 else v[:77] + "..."
            print(f"    /{k}: {short}")
    print(f"  metadata leaks : {len(leaks)}")
    for leak in leaks:
        print(f"    /{leak['field']} matched {leak['pattern']}")
    print(f"  fonts          : {n_fonts} total, {len(subset)} subset, {len(not_subset)} full")
    if font_warning:
        print(f"  WARN: {not_subset_ratio:.0%} of fonts are not subset (threshold {FONT_SUBSET_THRESHOLD:.0%})")
    print(f"  Results -> {out_path}")

    return 0 if not leaks else 1


if __name__ == "__main__":
    sys.exit(main())
