# Code of Conduct Attestation

The authors affirm that this submission complies with the NeurIPS Code of
Conduct. The work consists of theoretical analysis and computational
experiments on synthetic and publicly available data.

No human subjects data was collected, and no participant interactions were
conducted as part of this work. The experimental pipeline operates on
machine-generated inputs and on text from open-source corpora referenced
in the paper.

Agent provenance for the certificate pipeline is documented in
`AGENTS.md`. Each automated check reports its inputs, outputs, and
classification logic so that reviewers can reproduce and audit the
verdicts independently.

License terms for all direct dependencies of the certificate pipeline are
tracked in `ci/sbom_manifest.json`. Verdicts against an explicit
allowlist of NeurIPS-acceptable open-source licenses are produced by
`ci/license_clearance_check.py` and recorded in
`ci/license_clearance_results.json`.

The authors take responsibility for the contents of this submission and
for any subsequent revisions made during review.
