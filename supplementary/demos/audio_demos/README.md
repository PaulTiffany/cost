# Sonification Demos

Open `INDEX.html` in any browser to play the audio inline. The MP3s are
128 kbps re-encodes (2.3 MB total) of the original WAVs (12.5 MB total);
both are kept here. Regenerate from scratch with:

```
cd supplementary/demos
python sonification.py
```

The audio is generated deterministically from the geometric framework. The
interference amplitude `A(theta) = |cos(theta/2)|` that governs constraint
conflict in the paper is mathematically identical to two-wave superposition.
When you hear dissonance, you are hearing the diagonal cost.

## Listening guide

### Conflict at increasing rho

| File | rho | Listen for |
|---|---|---|
| `conflict_rho_0.00` | 0.00 | Clean separable tones, no beating |
| `conflict_rho_0.15` | 0.15 | Slow beating; near-consonant. The empirical safety+helpfulness value |
| `conflict_rho_0.30` | 0.30 | Noticeable beating; routing recipe boundary |
| `conflict_rho_0.50` | 0.50 | Rapid beating, audible roughness |
| `conflict_rho_0.70` | 0.70 | Dissonance dominates; predicted-infeasible high tier |
| `conflict_rho_0.90` | 0.90 | Harsh; bound diverges as rho approaches 1 |
| `conflict_sweep_0_to_0.9` | sweep | The cliff in motion. Note where music becomes noise |
| `phase_transition_0.4_to_0.6` | A/B | Same tone pair, two sides of the cliff, half-second silence between |

### Simultaneous vs staged

| File | Setup | Point |
|---|---|---|
| `simultaneous_rho_0.5` | rho=0.5, both at once | Diagonal cost audible |
| `staged_rho_0.5` | rho=0.5, sequential | Same constraints, on-axis path |
| `simultaneous_rho_0.8` | rho=0.8, both at once | Cliff dominates |
| `staged_rho_0.8` | rho=0.8, sequential | Staging benefit even at high rho |
| `comparison_simultaneous_vs_staged_all_four` | 4-principle headline | Unstable chord vs resolving arpeggio |

### Constitution Wheel (Circle of Fifths mapping)

The four principles are placed on the Circle of Fifths: Helpful (C),
Harmless (G), Honest (D), Autonomy (A). Adjacent positions are perfect
fifths (consonant); opposites are tritones (dissonant).

| File | Mapping |
|---|---|
| `principle_{helpful,harmless,honest,autonomy}` | Single-tone references |
| `pair_helpful_harmless` | C+G perfect fifth, consonant |
| `pair_helpful_honest` | C+D major second, mild tension |
| `pair_helpful_autonomy` | C+A major sixth, moderate |
| `pair_harmless_honest` | G+D perfect fifth, consonant |
| `pair_harmless_autonomy` | G+A major second, tension |
| `pair_honest_autonomy` | D+A perfect fifth, consonant |
| `constitution_wheel_full` | Full traversal of all four |
| `diagonal_{helpful_harmless, three_principles, all_four}` | Simultaneous attempts |
| `staged_all_four_resolution` | The same four notes resolved sequentially |
| `conflict_resolution_helpful_honest` | Tension resolved through staging |
| `conflict_resolution_harmless_autonomy` | Tension resolved through staging |

### Empirical anchor

| File | rho | Source |
|---|---|---|
| `safety_helpfulness_rho_0.15` | 0.15 | The constitution-analysis measured value |

## Why this is in the paper

The audio is supplementary, not load-bearing. The geometric claims stand
on the deterministic verifiers and the proofs in `app:proofs`. The
sonification is an invitation: the same math that produces a feasibility
cliff in token space produces an audible roughness in pressure-wave space,
because both are governed by the same interference geometry. If you hear
the cliff, you have validated one face of the framework against your own
ears. If you do not, the deterministic results in the body still apply.

See `app:sonification` in the paper and `Plomp & Levelt (1965)` on
critical-bandwidth dissonance for the perception-side grounding.
