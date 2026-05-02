Algorithm 1 storyboard

Purpose

This asset turns Algorithm 1 into a compact reviewer-facing chain:

- prompt text
- extracted constraints
- pairwise cosine / rho-hat
- budget ratio
- routing decision before generation

Files

- `storyboard_spec.json`: source-of-truth content for the storyboard
- `build_storyboard.py`: deterministic renderer

Render targets

- `rebuttal/figures/algorithm1_storyboard.png`
- `rebuttal/figures/algorithm1_storyboard.pdf`
- `submission_repo/figures/algorithm1_storyboard.pdf`

Usage

```powershell
python rebuttal/experiments/algorithm1_storyboard/build_storyboard.py
```

Design notes

- The rebuttal version should answer one reviewer ask, not teach the whole paper.
- Keep a single worked example and minimal prose.
- The values in the spec are illustrative storyboard values, not new empirical
  claims. Keep them aligned with the corresponding rebuttal text and the active
  Algorithm 1 branch logic.
- If the main-paper thresholds change, update `storyboard_spec.json` first and
  regenerate all outputs.
