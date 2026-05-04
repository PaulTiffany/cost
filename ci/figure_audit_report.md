# Figure Audit — NeurIPS 2026 Submission `main.tex`

Source paper: `paper\main.tex` (2,581 lines)
Build log: `paper\main.log` (PDF builds clean — no Overfull \hbox)
Audit date: 2026-05-02

---

## Top 5 issues to address (ranked by likely reviewer impact)

1. **Figure 7 (`fig:cross_model`) caption is 92 words — exceeds 80-word guideline.** This is the only over-limit caption. It bundles two distinct findings (model-agnosticism + soft-constraint robustness) into a single dense paragraph. Splitting the "Together: ..." synthesis into either a separate sentence in body text or trimming the parenthetical model list would land it under 80.
2. **Figure 7 panel (a) width is only 0.40\columnwidth** — `cross_model_cliff.pdf` is rendered at ~159 pt wide while its source is 12×6 inches at 13–15 pt fonts. After PDF scaling to 159 pt, axis tick labels likely render at ~3–4 pt. Worst case in the paper for in-figure text legibility. Companion panel (b) at 0.56\columnwidth has similar but milder concerns.
3. **Two `Underfull \vbox (badness 10000)` warnings around the bibliography output** (`main.log:1221, 1224`). Pages 9–10 are likely showing forced whitespace from a float that pushed past the column break — visible reviewer-side as awkward gaps after Figure 7 / before the appendix.
4. **TikZ in-figure text repeatedly uses `\tiny` (5pt) and `\scriptsize` (~7pt)** — pervasive across `figures/related_envelope_combined.tex` (8 limitation cards all `\tiny`), Figure 2 panels (`\tiny` axis labels), and Figure 6 (`\tiny` legend, axis tick labels, region labels). The combined related_envelope figure crams 16 hyperlinked citations + 8 limitation cards into 0.58 + 0.40 of `\linewidth` — at print scale this is borderline unreadable and triggered an `Underfull \hbox` warning at line 1216 (the "Action: 7 models" card text overflowing).
5. **Figure 5 (`fig:constrained_decoding`) width is 0.62\columnwidth** — borderline under-utilization; matplotlib source uses 13/12 pt fonts with `figsize=(10, 2.7)`, so the PDF gets requested at only 246 pt wide (vs. 711 pt native). Axis and tick text scales down ~2.9×, dropping effective tick fontsize to ~4 pt.

---

## Per-figure punch list

### Figure 1 (`fig:pipeline`) — Algorithm 1 storyboard
- Caption length: **49 words** (OK)
- Source: `rebuttal\experiments\algorithm1_storyboard\build_storyboard.py`
- Width: `\textwidth` (figure*, two-column span) — full utilization
- Font sizes (matplotlib): panel titles 12 pt bold, body labels 10–11.5 pt, sub-captions 8.5 pt. Type-1 fonts (`pdf.fonttype=42`). **Acceptable** since rendered at full text width.
- Build warnings: none specific
- Caption opening: `\textbf{Algorithm~\ref{alg:routing} visualized}` — bold opener present, uses \ref. **PASS**
- Issues: none material.

### Figure 2 (`fig:geometric_setup` / `fig:diagonal_cost` / `fig:worked_geometry`) — TikZ diagonal cost
- Caption length: **55 words** (OK)
- Source: inline TikZ (main.tex:132–193)
- Width: two `0.48\columnwidth` minipages
- Font sizes: `\scriptsize` and `\tiny` for axis labels (`r_1`, `r_2`, `u`, `v`, `Delta*`, `sqrt(2)delta`). At 0.48\columnwidth × scaled 0.85cm grid, these will render around 5–6 pt.
- Caption opening: `\textbf{Geometric setup.}` — **PASS**
- Issues:
  - **Triple `\label`** (`fig:geometric_setup`, `fig:diagonal_cost`, `fig:worked_geometry`) — only the last is the canonical hyperref target; cross-references like `Figure~\ref{fig:diagonal_cost}` (line 130, 195) are aliases for the same float. Functional but unusual; risk of confusion.
  - All in-figure math labels use `\tiny` — borderline at this size.

### Figure 3 (`fig:gram_eigendecomp`) — Gram eigendecomposition
- Caption length: **51 words** (OK)
- Source: `rebuttal\experiments\dct_analysis\gram_eigendecomposition.py`
- Width: `0.85\columnwidth` (single column)
- Font sizes (matplotlib): `font.size=14, axes.titlesize=15, axes.labelsize=14, xtick=12, ytick=12, legend=12`. Source is 15×11 inches (6 panels). After scaling to ~338 pt (vs. 907 pt native), effective fontsize ≈ 5.2 pt for ticks. **Concern**: 6-panel layout in a single-column slot is a heavy down-scale — labels may be marginal.
- Caption opening: `\textbf{Embedding-derived $\hat{\rho}$ ...}` — **PASS**
- Cross-refs: uses `Theorem~\ref{thm:gram}`, `Lemma~\ref{lem:proxy_sufficiency}` — **PASS**
- Issues:
  - 6-panel multi-axis figure compressed into 0.85\columnwidth is busy; consider 2 panels or making it a `figure*`.
  - Type-1 fonts NOT explicitly forced in this script (no `matplotlib.rcParams["pdf.fonttype"] = 42`). Risk of Type-3 fonts which violates NeurIPS submission rules. **Verify pdffonts output.**

### Figure 4 (`fig:per_task`) — Per-task correlation
- Caption length: **59 words** (OK)
- Source: `rebuttal\experiments\per_task_correlation.py`
- Width: `0.85\columnwidth` (single column)
- Font sizes (matplotlib): `font.size=14, axes.labelsize=14, xtick=12, ytick=12, legend=12`. Includes some inline `fontsize=10`/`fontsize=11` overrides for cell text. Source `figsize=(13, 5)` → requested 338 pt vs. native 923 pt = 2.7× downscale. Effective tick label size ~4.5 pt. **Concern.**
- Type-1 fonts: forced (`pdf.fonttype=42`) — PASS
- Caption opening: `\textbf{Per-task pass rate by $\hat{\rho}$ tier}` — **PASS**
- Cross-refs: `Table~\ref{tab:llm_bridge}`, `Appendix~\ref{app:code_full}` — **PASS**
- Issues: heavy downscale; per-cell heatmap annotations (`fontsize=10`) will render ~3.7 pt — may be illegible.

### Figure 5 (`fig:constrained_decoding`) — Constrained decoding
- Caption length: **53 words** (OK)
- Source: `rebuttal\experiments\constrained_decoding\plot_constrained_decoding.py`
- Width: **`0.62\columnwidth`** — borderline; under-utilizes column space
- Font sizes (matplotlib): `font.size=13, axes=13, xtick=12, ytick=12`, legend block uses `fontsize=15`. Source `figsize=(10, 2.7)` → log shows requested 246 pt vs. native 711 pt = **2.89× downscale**. Effective tick fontsize ≈ 4.2 pt. The legend "Standard"/"Constrained" labels at fontsize=15 will scale to ~5.2 pt.
- Type-1 fonts: NOT forced in this script — same concern as Fig 3.
- Caption opening: `\textbf{Constrained-decoding methods hit the same cliff.}` — **PASS**
- Cross-refs: caption text mentions "panels (a) and (b)" but no Theorem/Table refs needed — **PASS**
- Issues:
  - Width 0.62 is below "potentially under-utilizing" threshold of 0.6 only by 0.02; recommend bumping to `\columnwidth` or revising matplotlib aspect ratio.
  - Bar value annotations are `fontsize=9` → ~3 pt rendered. Likely illegible.

### Figure 6 (`fig:feasibility_surface` / `fig:boundary_vs_observed`) — TikZ feasibility surface
- Caption length: **46 words** (OK)
- Source: inline TikZ (main.tex:418–491) using pgfplots
- Width: minipages `0.44\columnwidth` (panel a) and `0.34\columnwidth` (panel b) — **both under 0.6**
- Font sizes: `tick label style={font=\tiny}`, `label style={font=\scriptsize}`, region labels (`1-shot`, `Stage`, `Fail`) at `\tiny\bfseries`, legend `\tiny`
- Caption opening: `\textbf{Feasibility surface and theory--observation alignment.}` — **PASS**
- Cross-refs: `Table~\ref{tab:calibration}` — **PASS**
- Issues:
  - Double `\label` (same float labeled twice).
  - Heights set to `3.4cm` — visually small; the `\tiny` tick labels (5 pt) on a sub-3cm-tall axis are at the edge of legibility.
  - Total figure occupies only 0.78\columnwidth of horizontal space (0.44 + 0.34); could be enlarged.

### Figure 7 (`fig:cross_model` / `fig:soft_constraint_cliff`) — cross-model + soft constraints
- Caption length: **92 words — OVER 80** ⚠
- Source (a): `rebuttal\experiments\cross_model\plot_cross_model.py`
- Source (b): `rebuttal\experiments\soft_constraints\soft_constraint_pilot.py`
- Width: minipages **`0.40\columnwidth`** (a) + **`0.56\columnwidth`** (b) — **both under 0.6** ⚠
- Font sizes:
  - (a) `font.size=13, axes.titlesize=15, axes.labelsize=13, xtick=12, ytick=12`. Source `figsize=(12, 6)` → requested 159 pt vs. native 451 pt = **2.84× downscale**. Effective tick fontsize ≈ 4.2 pt; cell-text annotations at `fontsize=11` → ~3.9 pt. **Worst legibility risk in the paper.** Title at 14 pt → ~4.9 pt.
  - (b) `font.size=13, axes=13, xtick=12, ytick=12`. Source `figsize=(7, 5)` → requested 223 pt vs. native 492 pt = 2.21× downscale. Effective tick fontsize ≈ 5.4 pt. Marginal.
- Type-1 fonts: (a) forced `pdf.fonttype=42`; (b) NOT forced.
- Caption opening: `\textbf{The cliff is model-agnostic and survives constraint softening.}` — **PASS**
- Cross-refs: `Theorem~\ref{thm:gram}`, `Table~\ref{tab:claude_family}`, `Appendix~\ref{app:charity}` — **PASS**
- Issues:
  - Caption length over budget.
  - Both panels under-utilize column width — together use 0.96\columnwidth but split unevenly.
  - Double `\label` again.
  - Heatmap font scaling on panel (a) is the worst in the document.

### Figure 8 (`fig:rw_quadrant` / `fig:limitations_envelope`) — Combined related-work + limitations envelope
- Caption length: **75 words** (OK, under 80 but close)
- Source: `paper\figures\related_envelope_combined.tex` (TikZ)
- Width: minipages `0.58\linewidth` (a) + `0.40\linewidth` (b)
- Font sizes: `\scriptsize` body, `\tiny` for citations, corner labels, all 8 limitation cards, and band labels. Quadrant frame is 10×5.5 cm × 0.7cm scale = ~7×3.85cm rendered.
- Caption opening: `\textbf{Where this work sits and where it stops.}` — **PASS**
- Cross-refs: `App~\ref{app:extended_rw}` — **PASS**
- Issues:
  - **`Underfull \hbox (badness 1082)`** at lines 106–107 ("`Action: 7 models 1B$\to$frontier`" card text) — text just barely fits its `text width=0.215\columnwidth` box.
  - All 8 limitation cards use `\tiny` body text inside narrow text-width boxes — risk of overflow or unreadable.
  - 16 citations packed into the quadrant at `\tiny` font; reviewers will need to zoom.

### Figure A1 (`fig:amplification`) — Appendix: Cost amplification curve
- Caption length: **30 words** (OK)
- Source: inline TikZ/pgfplots (main.tex:1748–1773)
- Width: `0.9\columnwidth`, height `5cm`
- Font sizes: `tick label style={font=\footnotesize}`, `label style={font=\small}` — well-sized
- Caption opening: `\textbf{Cost Amplification.}` — **PASS**
- Issues: none.

---

## Cross-cutting findings

### Build log warnings (`main.log`)
- `Overfull \hbox`: **0** ✓
- `Underfull \vbox` (around floats / output active): **8 occurrences**
  - line 1144, 1147 (badness 2229) — pp.1–2 transition, around algorithm1_storyboard placement
  - **line 1221, 1224 (badness 10000)** — bibliography output at p.9–10; likely related to Figure 7 + soft_constraint_cliff floating
  - line 1267, 1270 (badness 1286) — p.20 region (mid-appendix)
  - line 1311, 1314 (badness 2735) — p.35 region (deep appendix)
- `Underfull \hbox` (paragraph stretching): **4 occurrences**
  - line 1157 (badness 10000): paragraph at lines 280–281 — Corollary 3.1 statement, Greek letters can't hyphenate
  - line 1216 (badness 1082): lines 106–107 — `Action: 7 models 1B$\to$frontier` card in limitations envelope
  - line 1287 (badness 1796): lines 1739–1740 — Corollary M.25 "Curvature cannot defeat the bound"
  - line 1294 (badness 1430): lines 1865–1866 — triangle inequality math line in appendix

### Caption opener pattern
**All 9 figures use `\textbf{Title.}` opener — PASS uniformly.**

### Cross-references in captions
**All captions use `\ref{}` for Theorems, Tables, Appendices — no hardcoded "Theorem 3.4" found.** PASS.

### Type-1 font enforcement (NeurIPS / ICML rule against Type-3)
- `algorithm1_storyboard/build_storyboard.py`: forces `pdf.fonttype=42` ✓
- `cross_model/plot_cross_model.py`: forces `pdf.fonttype=42` ✓
- `per_task_correlation.py`: forces `pdf.fonttype=42` ✓
- `dct_analysis/gram_eigendecomposition.py`: **does NOT force** ✗
- `constrained_decoding/plot_constrained_decoding.py`: **does NOT force** ✗
- `soft_constraints/soft_constraint_pilot.py`: **does NOT force** ✗
Recommend running `pdffonts figures/*.pdf | grep Type` to confirm none are Type-3.

### Width utilization summary (under 0.6\columnwidth single-column)
| Figure | Source | Effective width | Native source | Downscale |
|---|---|---|---|---|
| Fig 5 | constrained_decoding.pdf | 0.62 cw (~246 pt) | 711 pt | 2.89× |
| Fig 6a | TikZ feasibility | 0.44 cw | — | n/a |
| Fig 6b | TikZ theory-vs-obs | 0.34 cw | — | n/a |
| **Fig 7a** | **cross_model_cliff.pdf** | **0.40 cw (~159 pt)** | **451 pt** | **2.84× (worst)** |
| Fig 7b | soft_constraint_cliff.pdf | 0.56 cw (~223 pt) | 492 pt | 2.21× |

### Multi-`\label` floats (cosmetic but worth noting)
- Figure 2: `\label{fig:geometric_setup}\label{fig:diagonal_cost}\label{fig:worked_geometry}`
- Figure 6: `\label{fig:feasibility_surface}\label{fig:boundary_vs_observed}`
- Figure 7: `\label{fig:cross_model}\label{fig:soft_constraint_cliff}`
All resolve to the same float number; consider consolidating to a single canonical label per figure.
