# The Cost of Cacophony

Geometric Limits on Multi-Constraint Alignment

**Preprint**: arXiv (cs.AI), CC BY 4.0
**Author**: Paul Carver Tiffany III ([ORCID](https://orcid.org/0009-0003-4785-6797))
**Paper**: [paper/main_arxiv.pdf](paper/main_arxiv.pdf) (63 pages)

This repository contains the arXiv preprint source, the verification harnesses for every empirical claim, the mechanical certificate that binds paper claims to result JSONs, and reproducibility tooling.

The paper proves a geometric lower bound on multi-constraint LLM satisfaction (the diagonal cost), derives a pre-generation routing algorithm from the bound, and validates the prediction across 5,472 trials and 17 LLMs under a deterministic audit observer. Empirical validation is judge-free.

This paper is one part of an in-progress research program. The papers in the program are independent contributions. This one stands on its own.

## For reviewers

- **Paper**: [paper/main_arxiv.pdf](paper/main_arxiv.pdf)
- **Reviewer index**: [supplementary/REVIEWER_INDEX.md](supplementary/REVIEWER_INDEX.md)
- **Quickstart**: [REVIEWER_QUICKSTART.md](REVIEWER_QUICKSTART.md)
- **Mechanical certificate**: [ci/claim_certificate.md](ci/claim_certificate.md)
- **Cost report**: [ci/cost_report.json](ci/cost_report.json)
- **Audio sonification suite** (browser-playable): [supplementary/demos/audio_demos/INDEX.html](supplementary/demos/audio_demos/INDEX.html)

## Repository layout

```
.
├── paper/                  arXiv source, rendered PDF, figures, bibliography
├── ci/                     verification harnesses, mechanical cert, cost report
├── supplementary/          reviewer aids, audio demos, illustrations
├── docs/                   reference reading and external documents
└── LICENSE                 CC BY 4.0
```

## Reproduce the certificate verdict

The full cert chain runs in roughly 30 seconds with stdlib only.

```
python ci/claim_certificate.py --venue arxiv
```

Expected: `PASS` across the active layer chain.

Spot checks for the headline empirical claims live in [REVIEWER_QUICKSTART.md](REVIEWER_QUICKSTART.md).

## Build the PDF

```
python paper/build_arxiv.py --package
```

This regenerates `paper/main_arxiv.tex` from `paper/main.tex` with arXiv author identity, compiles the PDF, and bundles `paper/cacophony_arxiv_source.tar.gz` for arXiv upload.

## License

This work is released under the Creative Commons Attribution 4.0 International License (CC BY 4.0). See [LICENSE](LICENSE) for the full text. You are free to share and adapt the material with attribution.
