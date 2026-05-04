#!/usr/bin/env python3
"""
build_arxiv.py

Generates paper/main_arxiv.tex from paper/main.tex with five substitutions:
  1. \\usepackage{neurips_2026}                -> \\usepackage[preprint]{neurips_2026}
  2. Anonymous Authors                          -> Paul Carver Tiffany III
  3. Anonymous Affiliation                      -> Independent Researcher
  4. anonymous@anonymous.org                    -> paulctiffany@gmail.com
  5. https://anonymous.4open.science/r/cacophony -> https://github.com/PaulTiffany/cacophony

This keeps the NeurIPS submission (paper/main.tex) anonymous and produces
an arXiv preprint variant (paper/main_arxiv.tex) with real author identity
and a distinct code URL. The distinct URL breaks the search-engine bridge
that would otherwise let a Google query on the 4open URL find the arXiv
preprint with author identity attached.

Usage:
    python paper/build_arxiv.py            # write main_arxiv.tex only
    python paper/build_arxiv.py --compile  # write + run latexmk
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "paper" / "main.tex"
DST = REPO_ROOT / "paper" / "main_arxiv.tex"

GH = "https://github.com/PaulTiffany/cost"

SUBS = [
    (r"^\\usepackage\{neurips_2026\}\s*$",
     r"\\usepackage[preprint]{neurips_2026}"),
    (r"^\s*Anonymous Authors\\\\$",
     r"  Paul Carver Tiffany III\\\\"),
    (r"^\s*Anonymous Affiliation\\\\$",
     r"  Independent Researcher\\\\"),
    (r"^\s*\\texttt\{anonymous@anonymous\.org\}$",
     r"  \\texttt{paulctiffany@gmail.com}"),
    # Code URL: anonymous 4open URL -> public GitHub URL with a generic name
    # (cost) to avoid bridging the NeurIPS submission's keyword (cacophony) to
    # this arXiv preprint via a Google search on the URL string.
    (r"Code: \\url\{https://anonymous\.4open\.science/r/cacophony\}\.",
     r"Code: \\url{" + GH + r"}."
     r"\\footnote{Direct artifact links: "
     r"\\href{" + GH + r"/blob/main/supplementary/demos/audio_demos/INDEX.html}{audio demos (browser-playable)}, "
     r"\\href{" + GH + r"/blob/main/ci/claim_certificate.md}{mechanical certificate}, "
     r"\\href{" + GH + r"/blob/main/ci/cost_report.json}{cost report}, "
     r"\\href{" + GH + r"/blob/main/supplementary/REVIEWER_INDEX.md}{reviewer index}.}"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compile", action="store_true",
                        help="run latexmk after writing main_arxiv.tex")
    args = parser.parse_args()

    text = SRC.read_text(encoding="utf-8")
    applied = []
    for pat, repl in SUBS:
        rx = re.compile(pat, re.MULTILINE)
        new_text, n = rx.subn(repl, text)
        if n == 0:
            print(f"WARNING: no match for: {pat[:70]}", file=sys.stderr)
        else:
            applied.append((pat[:50], n))
        text = new_text

    DST.write_text(text, encoding="utf-8")
    print(f"Wrote {DST.name}: {sum(n for _, n in applied)} substitutions across {len(applied)} patterns")
    for p, n in applied:
        print(f"  {n} match(es): {p}")

    if args.compile:
        print("\nRunning latexmk on main_arxiv.tex ...")
        cmd = ["latexmk", "-pdf", "-interaction=nonstopmode",
               "-file-line-error", "-jobname=main_arxiv", str(DST.name)]
        res = subprocess.run(cmd, cwd=REPO_ROOT / "paper",
                              capture_output=True, text=True, timeout=300)
        tail = res.stdout.splitlines()[-3:]
        for line in tail:
            print(line)
        if res.returncode != 0:
            print(f"\nlatexmk exited with {res.returncode}", file=sys.stderr)
            return 1
        out_pdf = REPO_ROOT / "paper" / "main_arxiv.pdf"
        if out_pdf.exists():
            size = out_pdf.stat().st_size
            print(f"\nWrote {out_pdf.name}: {size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
