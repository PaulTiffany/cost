# Ethics Statement

The author affirms that this work was conducted in accordance with standard
ethical research practices. The full Ethics, Broader Impacts, and Declaration
of LLM Usage section is in the paper itself (see `paper/ethics_arxiv.tex`,
rendered as a section of `paper/main_arxiv.pdf`).

In summary:

The work consists of theoretical analysis and computational experiments on
synthetic and publicly available data. No human subjects data was collected.
No participant interactions were conducted. The experimental pipeline operates
on machine-generated inputs and on text from open-source corpora cited in the
paper.

Agent provenance for the certificate pipeline is documented in `AGENTS.md`.
Each automated check reports its inputs, outputs, and classification logic so
that reviewers can reproduce and audit the verdicts independently.

License terms for all direct dependencies of the certificate pipeline are
tracked in `ci/sbom_manifest.json` and verified against an explicit allowlist
of open-source licenses by `ci/license_clearance_check.py`. Verdicts are
recorded in `ci/license_clearance_results.json`.

The author takes responsibility for the contents of this preprint and for any
subsequent revisions.
