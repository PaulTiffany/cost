# Compliance CI

Three checks must pass before submission. Each is its own script — run independently or chain via `make ci`.

## `claim_audit.py` (TODO)

Walks every numeric claim in `../paper/main.tex` and verifies it appears in `../CLAIM_AUDIT.md` with a working harness pointer. Failure modes the prior round hit:

- **Truncation:** never grep main.tex whole — paper is **~51 pages including appendix** (~2,560 source lines), will exceed any single context window. Split into at least 5 chunks for safe pattern reads, or use targeted line-range reads. Always verify what came back is current state, not stale.
- **Stale evidence:** when a claim's number changes but the audit text still shows the old value, the certificate becomes a lie. Audit must regenerate from paper, not from memory.

Output: report with ✓/⚠/✗ per claim, no auto-fixes.

## `page_check.py` (TODO)

Builds the paper, asserts body content ends ≤ page 9 and References starts ≤ page 10. Hard fail on any layout-hack commands (`\enlargethispage`, `\vspace*` with negative arg in body, etc.) — the page limit is honored by content, not by tricks.

## `anonymity_check.py` (TODO)

Greps the paper + supplementary for:
- Author names and affiliations
- Personal GitHub usernames
- ICML branding (`icml2026`, ICML stylesheet refs)
- Non-anonymous URLs (anything `github.com/<personal>` outside `anonymous.4open.science`)

Hard fail on hits in `paper/`, warn on hits in `supplementary/` (where some leak is acceptable in code comments).
