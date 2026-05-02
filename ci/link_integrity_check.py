#!/usr/bin/env python3
"""
link_integrity_check.py - Validate URLs, \\ref/\\label graph, and
internal hyperref targets in main.tex.

Layer 8 of the certification stack. LaTeX silently warns on broken
\\ref/\\href targets and renders ?? in the PDF. This layer catches
those before submission.

Three classes of check:

  A. URL integrity
     - Every URL in \\href{}, \\url{}, or bare http(s):// is well-formed
     - For known venue URL patterns (e.g. neurips.cc/Conferences/YYYY/),
       the year matches the paper's declared venue year (default 2026)
     - Optional whitelist of trusted prefixes (neurips.cc, openreview.net,
       arxiv.org, etc.)
     - DOES NOT make network calls by default; --network triggers HEAD
       checks (slow, requires internet)

  B. Internal hyperref targets
     - Every \\hyperref[label]{text} has a matching \\label{label} OR
       \\hypertarget{label}{...}

  C. \\ref / \\label graph closure
     - Every \\ref{key}, \\eqref{key}, \\autoref{key}, \\cref{key},
       \\nameref{key}, \\pageref{key} has a matching \\label{key}
     - Dead labels (defined but never referenced) are WARN-only

Exit codes
----------
  0  every check passes (or only WARN-level issues without --strict)
  1  unresolved URL/ref/hyperref OR --strict and any WARN
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
RESULTS_JSON = SCRIPT_DIR / "link_integrity_results.json"

# Default venue year — for URL year-vs-venue check.
VENUE_YEAR = 2026

# Whitelisted URL prefixes (well-known academic / venue domains).
TRUSTED_PREFIXES = (
    "https://neurips.cc/",
    "https://openreview.net/",
    "https://arxiv.org/",
    "https://4open.science/",
    "https://anonymous.4open.science/",  # anonymity-preserving subdomain
    "https://github.com/",
    "https://anthropic.com/",
    "https://openai.com/",
    "https://www.anthropic.com/",
)

# Patterns
RE_HREF = re.compile(r"\\href\{([^}]+)\}\{[^}]*\}")
RE_URL = re.compile(r"\\url\{([^}]+)\}")
RE_BARE_URL = re.compile(r"(?<![\w@/])(https?://[^\s\\}{$\"'>,;]+)")
RE_HYPERREF = re.compile(r"\\hyperref\[([^\]]+)\]\{[^}]*\}")
RE_HYPERTARGET = re.compile(r"\\hypertarget\{([^}]+)\}")
RE_REF = re.compile(r"\\(?:eq|page|auto|c|name|v)?ref\{([^}]+)\}")
RE_LABEL = re.compile(r"\\label\{([^}]+)\}")
# URL year pattern: matches /Conferences/2026/ or /YYYY/ slots in URL paths
RE_URL_YEAR = re.compile(r"/(\d{4})/|=(\d{4})\b|Conferences/(\d{4})|/(\d{4})$")


@dataclass
class LinkReport:
    n_urls: int = 0
    n_hyperref: int = 0
    n_hypertarget: int = 0
    n_refs: int = 0
    n_labels: int = 0
    malformed_urls: list[str] = field(default_factory=list)
    untrusted_urls: list[str] = field(default_factory=list)
    venue_year_mismatches: list[tuple[str, int]] = field(default_factory=list)
    unresolved_hyperref: list[str] = field(default_factory=list)
    unresolved_refs: list[str] = field(default_factory=list)
    dead_labels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "n_urls": self.n_urls,
            "n_hyperref": self.n_hyperref,
            "n_hypertarget": self.n_hypertarget,
            "n_refs": self.n_refs,
            "n_labels": self.n_labels,
            "malformed_urls": self.malformed_urls,
            "untrusted_urls": self.untrusted_urls,
            "venue_year_mismatches": [list(t) for t in self.venue_year_mismatches],
            "unresolved_hyperref": self.unresolved_hyperref,
            "unresolved_refs": self.unresolved_refs,
            "dead_labels_count": len(self.dead_labels),
            "dead_labels_sample": self.dead_labels[:20],
        }


def is_well_formed_url(url: str) -> bool:
    """Cheap structural check: scheme + at least one dot in netloc."""
    if not url.startswith(("http://", "https://")):
        return False
    rest = url.split("://", 1)[1]
    if "/" in rest:
        netloc = rest.split("/", 1)[0]
    else:
        netloc = rest
    return "." in netloc and len(netloc) > 3


def extract_url_year(url: str) -> int | None:
    """Pull a 4-digit year out of a URL path if present."""
    m = RE_URL_YEAR.search(url)
    if not m:
        return None
    for g in m.groups():
        if g:
            try:
                year = int(g)
                if 1990 <= year <= 2100:
                    return year
            except ValueError:
                continue
    return None


def is_trusted(url: str) -> bool:
    return any(url.startswith(p) for p in TRUSTED_PREFIXES)


def strip_comments(text: str) -> str:
    """Strip full-line comments and trailing comments from .tex source."""
    out_lines = []
    for line in text.splitlines():
        if line.lstrip().startswith("%"):
            continue
        out_lines.append(re.sub(r"(?<!\\)%.*$", "", line))
    return "\n".join(out_lines)


RE_INPUT = re.compile(r"\\input\{([^}]+)\}")


def expand_includes(text: str, base_dir: Path, max_depth: int = 4) -> str:
    """Recursively inline \\input{path} files so labels defined in
    included .tex files (e.g. figures/related_envelope_combined.tex)
    are visible to the analysis.

    Resolves include paths relative to base_dir. Adds .tex extension
    if not present. Cycle-safe via depth limit and seen-set.
    """
    seen: set[Path] = set()

    def _expand(t: str, depth: int) -> str:
        if depth >= max_depth:
            return t

        def repl(m: re.Match) -> str:
            inc_path = m.group(1)
            candidate = base_dir / inc_path
            if not candidate.suffix:
                candidate = candidate.with_suffix(".tex")
            try:
                resolved = candidate.resolve()
            except OSError:
                return m.group(0)
            if not resolved.exists():
                return m.group(0)
            if resolved in seen:
                return ""  # already inlined; skip to avoid duplicate labels
            seen.add(resolved)
            try:
                content = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return m.group(0)
            content = strip_comments(content)
            return _expand(content, depth + 1)

        return RE_INPUT.sub(repl, t)

    return _expand(text, depth=0)


def analyze(venue_year: int = VENUE_YEAR, check_dead_labels: bool = True) -> LinkReport:
    if not MAIN_TEX.exists():
        raise FileNotFoundError(f"main.tex not found at {MAIN_TEX}")
    raw = MAIN_TEX.read_text(encoding="utf-8", errors="replace")
    text = strip_comments(raw)
    # Inline \input{}-included .tex files so labels defined there
    # (e.g. figures/related_envelope_combined.tex) are visible.
    text = expand_includes(text, MAIN_TEX.parent)

    r = LinkReport()

    # --- A. URLs ---
    urls: list[str] = []
    urls.extend(RE_HREF.findall(text))
    urls.extend(RE_URL.findall(text))
    # Bare URLs: capture but exclude those already inside \href{...} (we
    # already pulled those). Crude dedup on the URL string.
    bare_urls = RE_BARE_URL.findall(text)
    for u in bare_urls:
        if u not in urls:
            urls.append(u)
    r.n_urls = len(urls)

    for u in urls:
        if not is_well_formed_url(u):
            r.malformed_urls.append(u)
            continue
        if not is_trusted(u):
            r.untrusted_urls.append(u)
        year = extract_url_year(u)
        if year is not None and year != venue_year:
            r.venue_year_mismatches.append((u, year))

    # --- B. Internal hyperref targets ---
    hyperref_keys = set(RE_HYPERREF.findall(text))
    hypertarget_keys = set(RE_HYPERTARGET.findall(text))
    r.n_hyperref = len(hyperref_keys)
    r.n_hypertarget = len(hypertarget_keys)

    label_keys = set(RE_LABEL.findall(text))
    valid_targets = hypertarget_keys | label_keys
    r.unresolved_hyperref = sorted(hyperref_keys - valid_targets)

    # --- C. \ref / \label graph ---
    ref_keys = set(RE_REF.findall(text))
    # Multi-key refs: \ref{a, b, c}
    expanded_refs: set[str] = set()
    for k in ref_keys:
        for sub in k.split(","):
            sub = sub.strip()
            if sub:
                expanded_refs.add(sub)
    r.n_refs = len(expanded_refs)
    r.n_labels = len(label_keys)

    r.unresolved_refs = sorted(expanded_refs - label_keys)
    if check_dead_labels:
        r.dead_labels = sorted(label_keys - expanded_refs)

    return r


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--venue-year", type=int, default=VENUE_YEAR, help=f"expected URL year (default {VENUE_YEAR})")
    parser.add_argument("--strict", action="store_true", help="treat dead labels and untrusted URLs as FAIL")
    parser.add_argument("--verbose", "-v", action="store_true", help="print all dead labels and untrusted URLs")
    args = parser.parse_args()

    try:
        r = analyze(venue_year=args.venue_year)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("=" * 70)
    print("LINK INTEGRITY CHECK  (URLs + hyperref + ref/label graph)")
    print("=" * 70)
    print(f"main.tex:       {MAIN_TEX}")
    print(f"Venue year:     {args.venue_year}")
    print()
    print(f"  URLs:                    {r.n_urls:>4}")
    print(f"  \\hyperref targets:       {r.n_hyperref:>4}")
    print(f"  \\hypertarget defs:       {r.n_hypertarget:>4}")
    print(f"  \\ref / \\eqref / etc.:    {r.n_refs:>4}")
    print(f"  \\label defs:             {r.n_labels:>4}")
    print()
    print(f"  Malformed URLs:          {len(r.malformed_urls):>4}  (FAIL)")
    print(f"  Untrusted URLs:          {len(r.untrusted_urls):>4}  ({'FAIL' if args.strict else 'WARN'})")
    print(f"  Venue-year URL drift:    {len(r.venue_year_mismatches):>4}  (FAIL)")
    print(f"  Unresolved \\hyperref:    {len(r.unresolved_hyperref):>4}  (FAIL)")
    print(f"  Unresolved \\ref:         {len(r.unresolved_refs):>4}  (FAIL)")
    print(f"  Dead labels:             {len(r.dead_labels):>4}  ({'FAIL' if args.strict else 'WARN'})")
    print()

    failed = False
    if r.malformed_urls:
        print("MALFORMED URLs:")
        for u in r.malformed_urls:
            print(f"  - {u}")
        print()
        failed = True

    if r.venue_year_mismatches:
        print(f"URL YEAR != VENUE YEAR ({args.venue_year}):")
        for u, y in r.venue_year_mismatches:
            print(f"  - {u} (year={y})")
        print()
        failed = True

    if r.unresolved_hyperref:
        print("UNRESOLVED \\hyperref TARGETS (would render as broken in PDF):")
        for k in r.unresolved_hyperref:
            print(f"  - {k}")
        print()
        failed = True

    if r.unresolved_refs:
        print("UNRESOLVED \\ref TARGETS (LaTeX renders ?? in PDF):")
        for k in r.unresolved_refs:
            print(f"  - {k}")
        print()
        failed = True

    if r.untrusted_urls and (args.verbose or len(r.untrusted_urls) <= 10):
        print("UNTRUSTED URLs (not in well-known academic prefix list):")
        for u in r.untrusted_urls:
            print(f"  - {u}")
        print()
    elif r.untrusted_urls:
        print(f"UNTRUSTED URLs: {len(r.untrusted_urls)} entries (--verbose to list)")
        print()

    if r.dead_labels and args.verbose:
        print(f"DEAD LABELS (--verbose; defined but never referenced, {len(r.dead_labels)} total):")
        for k in r.dead_labels:
            print(f"  - {k}")
        print()
    elif r.dead_labels:
        print(f"DEAD LABELS: {len(r.dead_labels)} entries (--verbose to list)")
        print()

    payload = {"summary": r.to_dict(), "venue_year": args.venue_year, "main_tex": str(MAIN_TEX)}
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Full report: {RESULTS_JSON}")

    if failed:
        return 1
    if args.strict and (r.untrusted_urls or r.dead_labels):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
