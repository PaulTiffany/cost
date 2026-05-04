# NeurIPS 2026 — The Cost of Cacophony

Self-contained NeurIPS submission package, structured as the next-generation autonomous publishing template.

## Layout

```
neurips/
├── paper/                  Single-PDF submission: main.tex, main.pdf, refs, sty, figures
├── supplementary/          Code & data supplement (zipped at submission)
│   ├── bridges/            Geometric verification harnesses (Theorem 3.1, 3.4, etc.)
│   ├── demos/              Interactive demos
│   ├── experiments/        Primary experiments
│   └── experiments_rebuttal/  Follow-up additions (cross-model, soft-constraints,
│                              constrained-decoding, proxy-ablation, hard-negatives, etc.)
├── docs/                   NeurIPS official docs + reference reading
├── ci/                     Compliance checks (claim audit, page check, anonymity check)
├── rebuttal_prep/          Pre-review prep workspace
└── rebuttal/               Live rebuttal workspace (post-Mar 25)
```

## Submission targets

- **Abstract:** May 4 AOE (~May 5 8am ET)
- **Full paper:** May 6 AOE (~May 7 8am ET)
- **Anonymous code/data:** anonymous.4open.science mirror (set up before submission)

## Build

```bash
cd paper
latexmk -pdf -interaction=nonstopmode main.tex
```

## CI checks (run before submitting)

```bash
python ci/claim_audit.py         # every numeric claim → verification harness
python ci/page_check.py          # body ≤ 9 pages, references on page 10+
python ci/anonymity_check.py     # no GitHub URLs / author names / venue branding
```

## Provenance

Paper content preserved through "geometric conversion" (figure compression, section merges) from a prior template. Follow-up experiments folded in as supplementary additions.
