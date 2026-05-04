# Agent Provenance

This note documents AI-agent assistance used in preparing the manuscript,
the mechanical certificate (`ci/`), and the supplementary materials.

It is provenance disclosure only. Empirical claims are supported by
deterministic verifiers, source data files, and the certificate scripts
listed in `ci/claim_certificate.json`.

## Human responsibility

Human authors retain final responsibility for:
- All scientific claims and their interpretation
- Experimental design choices
- Final manuscript text
- Submission decisions
- The integrity of source data files

Agents below assisted with implementation, drafting, and audit. They did
not independently authorize releases or replace author judgment.

## Agent roles

| Agent | Primary role | What it did NOT do |
|---|---|---|
| Claude (Anthropic) | Implementation engineering for the certificate stack (`ci/*.py`, manifest schemas, sub-checks); paper integration of audited drift fixes (4,800 → 4,272 reconciliation, cross-model count cleanup, citation restoration); anti-Claude prose passes; reviewer artifact generation. | Did not author scientific claims. Did not act as empirical judge. Did not authorize the bound, the experimental design, or the framing. |
| GPT (OpenAI, "Codex"-style usage) | Independent audit of the certificate chain; reviewer-surface critique; structured handoff JSONs (`notetoclaude.json`, `note2claude2.json`); cert-chain recommendations. | Wrote only the designated handoff JSON files during the audited phase. Did not modify paper text or production cert files. |
| GPT image model (`openai/gpt-5.4-image-2` via OpenRouter) | Generation of image-medium experimental subjects (image_transfer Run B/C/D); illustration draft exploration. Outputs are recorded as raw observations or draft images, not as evidence-grade renderings. | Image outputs are NOT used as a judge. Manual rubric scoring (with rubric hash and per-trial rationales) governs Pass B determinations for image-format claims. |
| OpenRouter-hosted LLMs | Experimental subjects in cross-model + regression + forbidden-pivot experiments (Claude family, Llama, DeepSeek, Gemini, Mistral, Qwen, etc.). | These models are studied subjects, not authors. Their outputs feed into deterministic AST / regex / format verifiers; LLMs are never the verdict layer for headline claims. |

## Boundaries

- LLMs were not used as judges for any headline empirical outcome.
- Quantitative claims are tied to source files and checker scripts listed in
  the cert payload (`ci/claim_certificate.json`'s `artifact_hashes`).
- Generated images, notebooks, and audio demos are classified by role in
  `ci/submission_surface_manifest.json`. Image-model outputs are role
  `raw_observation` or `internal_or_excluded`, never `evidence_asset`,
  unless they pass through deterministic or human-rubric verification.
- The claim registry (`CLAIM_AUDIT.md`) and the claim-data-ties manifest
  (`ci/claim_data_ties.json`) are the canonical sources for what the paper
  asserts. Cert layers L1, L9, and L15 verify those claims against paper
  text and source data; L20 enforces source-JSON provenance.

## Provenance summary

| Workstream | Agent/tool | Verification status |
|---|---|---|
| Paper prose editing | Claude (drafting) + human review | L1 audit (verbatim claim presence in main.tex) |
| Certificate implementation | Claude (code) + GPT (audit) | L11 script integrity smoke tests; mutation acceptance suite (`ci/tests/test_cert_mutations.py`, 9/9 pass) |
| Code-experiment subjects (1B-3B local) | Open-weight HuggingFace models | Deterministic AST / unit-test verifiers (`code_constraint_verifier.py`) |
| Cross-model frontier subjects | OpenRouter-hosted LLMs | Same deterministic verifiers, response captured to JSON with model_id provenance |
| Image-medium subjects | `openai/gpt-5.4-image-2` via OpenRouter | Manual rubric for Pass B (`image_transfer_runD_passB.json` with rubric_hash and per-trial rationale; `single_rater_warning: true`) |
| Schematic illustrations | TikZ / matplotlib (deterministic) + image-model drafts (exploration only) | L14 illustration lineage check; deterministic redraw is the certified asset, draft PNGs are role `provenance_source` |
| Forbidden-pivot pivot experiment | OpenRouter LLMs (6 models, code + text-described-image) | Deterministic AST verifier (code) and regex word-boundary verifier (image-description); no LLM-judge |

## Not included

This document deliberately does NOT include:
- Full chat or session transcripts
- API keys, usernames, or local machine paths with personal identifiers
- Speculative claims about agent capability beyond what is documented above
- Implication that agent assistance replaces author responsibility

## Maintenance

- Add this file (`AGENTS.md`) to `ci/submission_surface_manifest.json` as
  `role: provenance_source` (already present in the manifest schema).
- The `cert_anonymity_check.py` script enforces no personal identifiers
  appear in cert-shipped artifacts, including this file.
- If any agent role above changes during the rebuttal window or
  camera-ready prep, update this table and re-run
  `python ci/claim_certificate.py` to refresh the artifact hashes.

## Venue language (for paper checklist)

> AI-agent assistance was used for prose editing, implementation support,
> and independent audit of supplementary/certificate materials. The
> empirical outcomes are not LLM-judged: quantitative claims are tied to
> deterministic verifiers, source data, and the mechanical certificate. A
> compact agent-provenance note (`AGENTS.md`) is included in the
> supplementary bundle.
