#!/usr/bin/env python3
"""
build_arxiv.py

Generates paper/main_arxiv.tex from paper/main.tex with the substitutions
required to turn the NeurIPS anonymous double-blind source into an arXiv
preprint with real author identity, real code URL, and the NeurIPS-specific
scaffolding (checklist apparatus, "NeurIPS reproducibility guidelines"
header) replaced by self-contained content (ethics_arxiv.tex).

Substitutions:
  1. \\usepackage{neurips_2026}                  -> \\usepackage[preprint]{neurips_2026}
  2. Anonymous Authors                            -> AUTHOR_NAME (+ ORCID line)
  3. Anonymous Affiliation                        -> AUTHOR_AFFIL
  4. anonymous@anonymous.org                      -> AUTHOR_EMAIL
  5. https://anonymous.4open.science/r/cacophony  -> CODE_URL
  6. Acknowledgments placeholder                  -> ACK_LINE
  7. "Following NeurIPS reproducibility guidelines:" -> "Following standard reproducibility guidelines:"
  8. \\input{checklist.tex}                       -> \\input{ethics_arxiv.tex}

Usage:
    python paper/build_arxiv.py            # write main_arxiv.tex
    python paper/build_arxiv.py --compile  # also run latexmk
    python paper/build_arxiv.py --package  # also build cacophony_arxiv_source.tar.gz

Exit:
    0 if all substitutions applied (at least one match each)
    1 if any substitution had zero matches (drift between main.tex and the
      regex set) or latexmk failed
"""
import argparse
import re
import subprocess
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PAPER = REPO_ROOT / "paper"
SRC = PAPER / "main.tex"
DST = PAPER / "main_arxiv.tex"

# Real author identity for the arXiv preprint.
AUTHOR_NAME = "Paul Carver Tiffany III"
AUTHOR_AFFIL = "Independent Researcher"
AUTHOR_EMAIL = "paulctiffany@gmail.com"
ORCID = "0009-0003-4785-6797"
ORCID_URL = f"https://orcid.org/{ORCID}"

# Public code mirror for the arXiv preprint.  Distinct from the anonymous
# 4open.science URL used in the NeurIPS submission so a Google query on the
# submission URL does not bridge to this preprint.
GH = "https://github.com/PaulTiffany/cost"
PAGES = "https://PaulTiffany.github.io/cost"

ACK_LINE = (
    "The author received no external funding for this work and "
    "reports no competing interests."
)

SUBS = [
    (r"^\\usepackage\{neurips_2026\}\s*$",
     r"\\usepackage[preprint]{neurips_2026}"),
    (r"^\s*Anonymous Authors\\\\$",
     f"  {AUTHOR_NAME}\\\\\\\\"),
    (r"^\s*Anonymous Affiliation\\\\$",
     f"  {AUTHOR_AFFIL}\\\\\\\\\n"
     f"  \\\\texttt{{{AUTHOR_EMAIL}}}\\\\\\\\"),
    (r"^\s*\\texttt\{anonymous@anonymous\.org\}$",
     f"  ORCID: \\\\href{{{ORCID_URL}}}{{{ORCID}}}"),
    # Code URL: anonymous 4open URL -> public GitHub URL.
    (r"Code: \\url\{https://anonymous\.4open\.science/r/cacophony\}\.",
     r"Code: \\url{" + GH + r"}."
     r"\\footnote{Direct artifact links: "
     r"\\href{" + PAGES + r"/supplementary/demos/audio_demos/INDEX.html}{audio demos (browser-playable)}, "
     r"\\href{" + GH + r"/blob/master/ci/claim_certificate.md}{mechanical certificate}, "
     r"\\href{" + GH + r"/blob/master/ci/cost_report.json}{cost report}, "
     r"\\href{" + GH + r"/blob/master/supplementary/REVIEWER_INDEX.md}{reviewer index}.}"),
    (r"\[Acknowledgments placeholder[^\]]+\]",
     ACK_LINE),
    (r"^Following NeurIPS reproducibility guidelines:$",
     r"Following standard reproducibility guidelines:"),
    # Ethics section replaces the checklist apparatus entirely.  Content lives
    # in ethics_arxiv.tex (kept as a sister file so the LaTeX source stays
    # readable and the migration is auditable).
    (r"\\input\{checklist\.tex\}",
     r"\\input{ethics_arxiv.tex}"),
]


def _apply_subs(text: str) -> tuple[str, list[tuple[str, int]], list[str]]:
    applied: list[tuple[str, int]] = []
    missing: list[str] = []
    for pat, repl in SUBS:
        rx = re.compile(pat, re.MULTILINE)
        new_text, n = rx.subn(repl, text)
        if n == 0:
            missing.append(pat[:80])
        else:
            applied.append((pat[:60], n))
        text = new_text
    return text, applied, missing


def _build(args) -> int:
    if not SRC.exists():
        print(f"ERROR: source not found: {SRC}", file=sys.stderr)
        return 1
    text = SRC.read_text(encoding="utf-8")
    text, applied, missing = _apply_subs(text)
    DST.write_text(text, encoding="utf-8")
    print(f"Wrote {DST.relative_to(REPO_ROOT)}: "
          f"{sum(n for _, n in applied)} substitutions across {len(applied)} patterns")
    for p, n in applied:
        print(f"  {n} match(es): {p}")
    if missing:
        print(f"\nWARNING: {len(missing)} pattern(s) had no match. Source has drifted:",
              file=sys.stderr)
        for p in missing:
            print(f"  no match: {p}", file=sys.stderr)
        return 1
    return 0


def _compile() -> int:
    # Manual pdflatex/bibtex/pdflatex/pdflatex cycle.  latexmk's exit code is
    # poisoned by pdflatex returning 1 on undefined-refs in any single pass,
    # even though the iterated cycle would resolve them.  Doing the cycle
    # explicitly lets us judge by the final pdflatex's ref count, not by
    # latexmk's terminal status.
    print("\nCompiling main_arxiv.tex (pdflatex -> bibtex -> pdflatex x2) ...")
    pdflatex = ["pdflatex", "-interaction=nonstopmode", "-file-line-error",
                "-jobname=main_arxiv", DST.name]
    bibtex = ["bibtex", "main_arxiv"]
    for cmd in (pdflatex, bibtex, pdflatex, pdflatex):
        subprocess.run(cmd, cwd=PAPER, capture_output=True, text=True, timeout=600)
    log = (PAPER / "main_arxiv.log").read_text(encoding="utf-8", errors="ignore")
    unresolved = [l for l in log.splitlines() if "undefined on input line" in l]
    if unresolved:
        print(f"\nWARNING: {len(unresolved)} unresolved references remain:",
              file=sys.stderr)
        for line in unresolved[:10]:
            print(f"  {line.strip()}", file=sys.stderr)
        return 1
    out_pdf = PAPER / "main_arxiv.pdf"
    if not out_pdf.exists():
        print(f"\nERROR: no PDF produced", file=sys.stderr)
        return 1
    size = out_pdf.stat().st_size
    pages = "?"
    m = re.search(r"Output written on main_arxiv\.pdf \((\d+) pages", log)
    if m:
        pages = m.group(1)
    print(f"Wrote {out_pdf.relative_to(REPO_ROOT)}: {pages} pages, {size:,} bytes")
    return 0


# Files to ship in the arXiv source tarball.  Paths are relative to PAPER.
TARBALL_INCLUDE = [
    "main_arxiv.tex",
    "main_arxiv.bbl",
    "ethics_arxiv.tex",
    "extended_related_work.tex",
    "references.bib",
    "neurips_2026.sty",
    "algorithm.sty",
    "algorithmic.sty",
]
TARBALL_INCLUDE_GLOBS = [
    "figures/*.tex",
    "figures/*.pdf",
    "figures/*.png",
]


def _package() -> int:
    tar_path = PAPER / "cacophony_arxiv_source.tar.gz"
    missing = [f for f in TARBALL_INCLUDE if not (PAPER / f).exists()]
    if missing:
        print(f"ERROR: required tarball files missing: {missing}", file=sys.stderr)
        return 1
    n_added = 0
    with tarfile.open(tar_path, "w:gz") as tf:
        for rel in TARBALL_INCLUDE:
            tf.add(PAPER / rel, arcname=rel)
            n_added += 1
        for pattern in TARBALL_INCLUDE_GLOBS:
            for p in sorted(PAPER.glob(pattern)):
                tf.add(p, arcname=str(p.relative_to(PAPER)).replace("\\", "/"))
                n_added += 1
    size = tar_path.stat().st_size
    print(f"\nWrote {tar_path.relative_to(REPO_ROOT)}: "
          f"{n_added} entries, {size:,} bytes")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--compile", action="store_true",
                        help="run latexmk after writing main_arxiv.tex")
    parser.add_argument("--package", action="store_true",
                        help="build cacophony_arxiv_source.tar.gz after compile")
    args = parser.parse_args()
    rc = _build(args)
    if rc != 0:
        return rc
    if args.compile or args.package:
        rc = _compile()
        if rc != 0:
            return rc
    if args.package:
        rc = _package()
    return rc


if __name__ == "__main__":
    sys.exit(main())
