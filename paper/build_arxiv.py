#!/usr/bin/env python3
"""
build_arxiv.py

Generates paper/main_arxiv.tex from paper/main.tex with five substitutions:
  1. \\usepackage{neurips_2026}                -> \\usepackage[preprint]{neurips_2026}
  2. Anonymous Authors                          -> <redacted_author>
  3. Anonymous Affiliation                      -> Independent Researcher
  4. anonymous@anonymous.org                    -> <redacted_email>
  5. https://anonymous.4open.science/r/cacophony -> https://github.com/<redacted_user>/cacophony

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

GH = "https://github.com/<redacted_user>/cost"
PAGES = "https://<redacted_user>.github.io/cost"  # GitHub Pages site for browser-rendered HTML

SUBS = [
    (r"^\\usepackage\{neurips_2026\}\s*$",
     r"\\usepackage[preprint]{neurips_2026}"),
    (r"^\s*Anonymous Authors\\\\$",
     r"  <redacted_author>\\\\"),
    (r"^\s*Anonymous Affiliation\\\\$",
     r"  Independent Researcher\\\\"),
    (r"^\s*\\texttt\{anonymous@anonymous\.org\}$",
     r"  \\texttt{<redacted_email>}"),
    # Code URL: anonymous 4open URL -> public GitHub URL with a generic name
    # (cost) to avoid bridging the NeurIPS submission's keyword (cacophony) to
    # this arXiv preprint via a Google search on the URL string.
    (r"Code: \\url\{https://anonymous\.4open\.science/r/cacophony\}\.",
     r"Code: \\url{" + GH + r"}."
     r"\\footnote{Direct artifact links: "
     r"\\href{" + PAGES + r"/supplementary/demos/audio_demos/INDEX.html}{audio demos (browser-playable)}, "
     r"\\href{" + GH + r"/blob/main/ci/claim_certificate.md}{mechanical certificate}, "
     r"\\href{" + GH + r"/blob/main/ci/cost_report.json}{cost report}, "
     r"\\href{" + GH + r"/blob/main/supplementary/REVIEWER_INDEX.md}{reviewer index}.}"),
    # Strip submission-flow meta-instructions (acknowledgments placeholder is a
    # NeurIPS-anonymized-review artifact; in the arXiv preprint the section is
    # visible, so replace the placeholder with a real one-line disclosure).
    (r"\[Acknowledgments placeholder[^\]]+\]",
     r"The author received no external funding for this work and reports no competing interests."),
    # Reproducibility section header: drop the venue-specific qualifier.
    # The handbook citation (~\\cite{neurips2026handbook}) and "11 NeurIPS
    # SOCIETAL IMPACT topics" stay as-is because they are content (citing
    # real documents), not submission-flow signals.
    (r"^Following NeurIPS reproducibility guidelines:$",
     r"Following standard reproducibility guidelines:"),
    # Re-point checklist input to the renamed sister file.
    (r"\\input\{checklist\.tex\}",
     r"\\input{checklist_arxiv.tex}"),
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

    # Sister file: checklist_arxiv.tex with the venue-prefixed title softened.
    # The questions still reference "NeurIPS Code of Ethics" etc. because
    # those are content (real documents being cited); the title is the only
    # cosmetic edit.
    check_src = REPO_ROOT / "paper" / "checklist.tex"
    check_dst = REPO_ROOT / "paper" / "checklist_arxiv.tex"
    if check_src.exists():
        ctext = check_src.read_text(encoding="utf-8")
        ctext = ctext.replace(
            r"\section*{NeurIPS Paper Checklist}",
            r"\section*{Paper Checklist}",
        )
        check_dst.write_text(ctext, encoding="utf-8")
        print(f"Wrote {check_dst.name}: title 'NeurIPS Paper Checklist' -> 'Paper Checklist'")

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
