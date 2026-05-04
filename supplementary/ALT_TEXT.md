# Figure Alt Text and Visual Accessibility Manifest

This file gives a screen-reader-friendly text description for every figure
in the paper and supplementary materials. The descriptions are written so
that a reader who cannot see the figure still gets the geometric reading
the figure was meant to support. Where a figure has a corresponding data
table elsewhere in the paper, the table reference is named explicitly.

If a reviewer prefers a single audit pass: read this file top-to-bottom,
then read the captions of the corresponding figures in the PDF.

## Visual choices applied paper-wide

- **Color choices.** Where heatmaps are used, the colormap is `cividis`
  (monotonic luminance, color-vision-deficiency safe, also reads in
  greyscale). The two-line cliff curves use the Wong / Okabe-Ito blue
  (`#0072B2`) and orange (`#D55E00`) pair, which is distinguishable for
  all common forms of CVD. Marker shape (circle vs square) and dash style
  (solid vs dashed) carry the same signal as color.
- **Redundant encoding.** Every heatmap cell shows its numeric value AND
  a shape marker (filled / half / hollow / cross) so a printout in
  greyscale or a CVD viewer still parses correctly.
- **Print-friendly.** PDF figures use vector formats (`.pdf`) where
  possible; raster fallbacks (`.png`) are 160 dpi minimum.
- **Caption-first reading.** Every paper figure caption is written to
  stand alone. A reader scanning only captions should still receive the
  framework's main argument.

## Body figures (in main.tex order)

### Figure 1 (line 97): Algorithm 1 storyboard
*Asset: `paper/figures/algorithm1_storyboard.pdf`.*
A four-panel sequence showing the routing pipeline applied to the running
example "be comprehensive vs be concise". Panel 1: prompt parsing into
two atomic constraints. Panel 2: each constraint embedded as a vector;
the angle between vectors yields the regime index rho-hat at
approximately 0.42. Panel 3: budget-ratio computation places the cell in
the staging band (delta over delta-min approximately 1.4). Panel 4: the
router selects "Staged" before any token is generated. The whole sequence
runs without an LLM-as-judge step.

### Figure 2 (line 197): Geometric setup, two panels
*Asset: TikZ in main.tex.*
Panel a: two halfspaces overlap to form a wedge-shaped feasible region;
a unit budget ball touches each halfspace edge along an axis but reaches
the wedge interior only along a diagonal of length sqrt(2) times delta.
The panel shows visually why the cost amplifies as sqrt(2 / (1 - rho))
when the constraints are correlated. Panel b: at rho = 0.5 with tau = 1
and unit budget, the minimum-norm feasible step has L2 norm 2.0,
matching the analytical bound.

### Figure 3 (line 241): Gram eigendecomposition
*Asset: `paper/figures/gram_eigendecomposition.pdf`.*
A two-panel plot. Left: smallest eigenvalue of the conflict-coupling
Gram matrix, lambda_min(B_rho) = 1 - rho(k - 1), plotted against rho for
k = 2, 3, 4. Each curve crosses zero at rho = 1/(k - 1), the divergence
point of the cost amplification. Right: the embedding cosine index
rho-hat preserves the rank ordering of the gradient-derived rho across
48 measurement points (Spearman r_s = 1.0).

### Figure 4 (line 360): Per-task pass rate by rho-hat tier
*Asset: `paper/figures/per_task_correlation.pdf`.*
Two-panel figure. Left: a 12-by-4 heatmap (12 code tasks by 4 rho-hat
tiers) with pass rate as cell color. The high-conflict (rightmost) column
is uniformly low pass; the low-conflict (leftmost) column is uniformly
high. Right: aggregate pass rate per tier with 95 percent bootstrap
confidence intervals; the four bars trace a sharp monotonic decline.
Spearman r_s = -0.942, p approximately 2e-23, n = 48 points.

### Figure 5 (line 367): Constrained-decoding methods
*Asset: `paper/figures/constrained_decoding.pdf`.*
Two side-by-side panels comparing Outlines, Guidance, and JSON-mode
constrained decoding. Both panels show the same sqrt(2/(1-rho))
amplification curve, demonstrating that grammar enforcement does not
move the cliff. The shared key on the right makes the comparison
explicit: the underlying optimization landscape is the same regardless
of which token-masking scheme is layered on.

### Figure 6 (line 410): Calibration benchmark
*Asset: TikZ.*
A staircase plot: failure rate increases monotonically with regime index
rho-hat across four calibrated tiers. Aggregate across 4 models, n = 240
trials per tier, error bars are 95 percent bootstrap confidence
intervals. Spearman r_s = 1.0.

### Figure 7 (line 495): Feasibility surface, two panels
*Asset: TikZ.*
Panel a: a 2D heatmap of pass rate over (rho-hat, delta over delta-min),
with the routing region boundaries overlaid as dashed contours. The
lower-right region (high rho-hat, low budget ratio) is uniformly low
pass; the boundary follows the predicted cliff. Panel b: the predicted
sqrt(2/(1-rho)) curve overlaid with four observed cliff centroids; the
match is visually exact at rho-hat approximately 0.4.

### Figure 8 (line 524, 529): Cross-model and soft-constraint cliffs
*Assets: `paper/figures/cross_model_cliff.pdf`, `paper/figures/soft_constraint_cliff.pdf`.*
Panel a: a 9-row heatmap (9 models from TinyLlama 1B to frontier API
models from 6 providers) by 4 rho-hat tiers, with pass rate as cell
color. The cliff appears at the same rho-hat regardless of model size or
provider. Panel b: replacing the binary verifier with a continuous
satisfaction score reproduces the same phase transition. The figure
makes the "geometry, not capability" argument visually.

## Body tables (line numbers as anchors)

For each numeric table the alt-text equivalent is the table itself; the
relevant entries to call out for a non-visual reader are listed.

### Table tab:claude_family (line 638): 10-model claude family
Read as a 10-row by 5-column table. Columns: Model, Generation, Wins
(staged-beats-oneshot out of 6 tasks), Ratio (mean displacement
improvement), Note. Rows: haiku-3 (3, 3/6, 2.5x, legacy baseline);
sonnet-4 (4, 5/6, 4.4x); opus-4 (4, 5/6, 22.7x); opus-4.1 (4, 5/6, 26.4x,
highest ratio); haiku-4.5 (4.5, 5/6, 23.3x); sonnet-4.5 (4.5, 5/6, 23.8x,
exact delta zero on one task); opus-4.5 (4.5, 4/6, 4.8x, best one-shot);
opus-4.6 (4.6, 4/6, 7.7x, added 2026-05); sonnet-4.6 (4.6, 6/6, 10.9x,
only model to sweep paired tasks); opus-4.7 (4.7, 5/6, 4.9x, added
2026-05, sampling caveat).

### Table tab:policy_density_curve (app:policy_density)
8-row by 4-column table. Tier T1 through T8, k = 2/5/8/12/16/22/28/34,
mean one-shot pass rate across 9 models and mean staged pass rate.
Two transitions: at T2 (k=5) staged jumps from 26 percent to 93 percent
(staging rescue); at T6 (k=22) staged collapses from 74 percent (one-
shot) down to 15 percent (staging inversion). At T8 (k=34) one-shot is
48 percent and staged is 22 percent.

## Showcase plots (May 2026 family extension)

### `supplementary/demos/figures/new_finding_1_policy_density_cliff_curve.png`
A two-line plot of mean all-pass rate (y axis, 0 to 100 percent) against
policy density k (x axis, values 2, 5, 8, 12, 16, 22, 28, 34). One-shot
is the blue solid-line series with circle markers; staged is the orange
dashed-line series with square markers. Two annotation arrows mark
"staging rescue" (at k = 5, where staged sits at 93 percent and one-shot
at 26 percent) and "staging inversion" (at k = 22, where one-shot is at
74 percent and staged collapses to 15 percent). Past k = 22 staged stays
below one-shot for the rest of the curve.

### `supplementary/demos/figures/new_finding_2_per_model_cliff_matrix.png`
A 9-row by 8-column heatmap. Rows are the 9 working Claude models, sorted
alphabetically. Columns are the 8 policy tiers (T1 / k = 2 through T8 /
k = 34). Cells encode the per-model one-shot pass rate three ways at
once: the numeric percentage in bold, a shape marker (filled circle for
>= 90 percent, half-filled for 60-89, hollow circle for 30-59, cross
for < 30), and the cividis color (dark blue at 0, bright yellow at 100,
monotonic luminance throughout). The most legible row is opus-4.1: every
cell from T3 onward is filled-circle 100 percent. No other model in the
panel sweeps T3 through T8 on one shot. The legend at the bottom names
the marker key explicitly.

### `supplementary/demos/figures/new_finding_3_tagger_calibration.png`
A scatter plot. X axis is mean extracted k per model (range
approximately 7 to 21, "tagger aggressiveness"). Y axis is the pipeline
all-pass rate over 8 implicit prompts (0 to 100 percent). Each point is
one model, labelled with its name and pipeline pass count out of 8. A
dashed grey linear fit slopes downward from upper-left to lower-right,
slope approximately negative 4.8 percentage points per unit of extracted
k. Lower extraction tracks higher pass. Most conservative tagger:
opus-4.1 at mean k = 7.1, 8 of 8 pass. Most aggressive: haiku-4.5 at
mean k = 21.2, 2 of 8 pass.

## Audio demos

See `supplementary/demos/audio_demos/INDEX.html` for the browser-playable
index with a written description for each clip. The HTML page has
semantic landmarks (header / nav / main), a skip-to-content link, a
table of contents with anchor IDs per section, and an `aria-label` on
each `<audio>` element so screen readers announce the clip name. The
audio is supplementary, never load-bearing; the deterministic results in
the body do not depend on it.

A summary of the listening guide appears in
`supplementary/demos/audio_demos/README.md`.
