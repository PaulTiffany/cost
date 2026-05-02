# De-LLM Lexicon: Protected Terms and Legitimate Targets

Reference for any pass (human or agent) that touches paper or supplementary prose. Two sides:
1. **Protected terms** — load-bearing for NeurIPS area-signal, AGI-26 lineage, and Principia Symbolica program. Do not vary. Recurrence is the point.
2. **Legitimate de-LLM targets** — actual LLM tells. Vary or remove with calculated variance.

Principle throughout: **never mechanical, always semantic**. The patterns are tells; the meaning is sacred. Each rewrite asks: is this instance defining, proving, measuring, or limiting? If it is doing the same job as a nearby instance, vary. If each is doing a different job, leave.

---

## 1. Protected terms (DO NOT vary, count, or compress)

### 1a. AGI-26 inheritance (verbatim quotes from the in-flight follow-on paper)

AGI-26 (`C:\src\cosmogenesis\agi2026\build\main.pdf`) explicitly cites cacophony as `[1]` and quotes these phrasings. Any drift here breaks the lineage.

| Phrase | Why protected |
|--------|---------------|
| `feasibility cliff` | AGI-26 abstract + intro use it verbatim |
| `$\delta_{\min} \geq (\tau/m)\sqrt{k/(1-\rho(k-1))}$` and `diverging at $\rho = 1/(k-1)$` | AGI-26 reprints the formula and divergence point |
| `sharp geometric boundary, not a gradual degradation` | AGI-26 quotes this framing line |
| `$\hat{\rho}$, computable from constraint text alone without generation` | AGI-26 inherits this exact ZO characterization |
| `detects proximity to this cliff` | AGI-26 phrase pattern |
| `judge-free validation` | AGI-26 keyword (abstract) |
| `collapse diagnostic $\hat{\rho}$` | AGI-26 alternative naming |
| `establishes when integration becomes mandatory` | Defines cacophony's role in the publication program |

### 1b. NeurIPS area-signal (helps reviewers categorize as ZO/black-box optimization)

| Phrase / pattern | Why protected |
|------------------|---------------|
| `zeroth-order` / `gradient-free` / `black-box` | Categorization signal for NeurIPS ZO area |
| `judge-free verifiers` / `judge-free validation` | The empirical-method discriminator |
| `regime index` / `regime structure` / `regime-driven` | The ρ̂ identity |

### 1c. Conceptual scaffold (the spine of the argument)

| Phrase / pattern | Why protected |
|------------------|---------------|
| `diagonal cost` | The core bound's name |
| `feasibility cliff` | The phenomenology |
| `geometric floor` | The high-ρ̂ collapse claim |
| `fail-safe routing` | The safety inheritance AGI-26 needs |
| `not capability gradient` | The rebuttal-to-skeptics frame |
| `kinematic` | The it-is-not-statistical claim (Significance line) |
| `Meta-theorem` | Explicit AGI-26 hook (in operating envelope figure caption) |
| `pre-generation routing` | The Algorithm 1 promise |

### 1d. Numeric anchors (every number in the paper is in CLAIM_AUDIT.md; do not let prose drift these)

- `0/4,800` — smooth-regime refutations
- `r_s = 1.0` — regime ordering
- `4.8x` — staging benefit at frontier
- `89% / 11%` — smooth / pivot mixture
- `94%` — router agreement
- `$<2\%$ regret` — vs oracle
- `1/(k-1)` — divergence threshold

---

## 2. Legitimate de-LLM targets (vary or remove, semantic judgment per instance)

### 2a. Hedging (cut or replace with concrete claim)

- `we believe`, `we argue`, `arguably`, `perhaps`, `it seems that`, `it appears`
- `to some extent`, `in some sense`, `in a way`
- `relatively`, `quite`, `fairly` as modifiers on technical claims

### 2b. Throat-clearing (remove or fold into the next sentence)

- `It is important to note that...`
- `Notably,` / `Importantly,` / `Crucially,` / `Interestingly,`
- `As mentioned (above|earlier|previously),`
- `In this paper, we...` (when redundant with section context)
- `The key insight is that...`

### 2c. Generic connectors (replace with semantic transition or period)

- `Moreover,` / `Furthermore,` / `Additionally,` / `In addition,`
- `Thus,` / `Hence,` / `Therefore,` when not actually marking deduction
- `On the other hand,` when there is no other hand

### 2d. Punctuation tells

- Em dashes `---` (mostly already removed; keep an eye)
- Semicolons used as soft connectors where a period would read cleaner
- Excessive parenthetical interjections (`(see X)`, `(cf. Y)`) mid-sentence

### 2e. Structural tells

- `(1) ... (2) ... (3) ...` constructions when the items are not actually a list
- Triplet rhythm: `X, Y, and Z` where one item is filler
- Parallel adjective stacking: `efficient, scalable, and robust`
- "Not only X but also Y" — almost always lobotomizable

### 2f. Voice / register

- Passive voice in declaratives where active is sharper (`is shown` → `we show` or just state the result)
- Future-tense intent (`We will show that...`) where present-tense statement works
- Royal `we` accumulating to suggest more authors than there are (cap at necessary instances)

---

## 3. Calculated variance (the craft direction)

When a protected term recurs, that is signal — leave it. When a legitimate target recurs, vary by:

1. **Sentence function**: this instance defines, the next measures, the next limits. Different functions warrant different phrasings.
2. **Length**: alternate short and long sentences. LLM cadence is uniform; human cadence varies.
3. **Concreteness**: where the LLM hedges, name the concrete.
4. **Voice**: alternate first-person plural with impersonal where natural.

Never apply a single substitution mechanically. If you find yourself running the same find/replace twice, stop and ask whether you are introducing a new tell.

---

## 4. NeurIPS-Oral lens

Cacophony is targeting the page limit but also reading aloud. Each section should ask:

- Would this paragraph survive being read aloud at a conference?
- Are there sentences that only work because the reader can scan back?
- Is the rhythm conversational or robotic?

Where the answer is "robotic," prefer the de-LLM swap even if the original was technically fine.

---

## 5. Operational checklist for a pass

Before editing a section:

1. Skim it once for protected terms (Section 1) — note their positions, do not touch.
2. Skim again for legitimate targets (Section 2) — collect candidates.
3. For each candidate, judge per Section 3.
4. After editing, confirm:
   - All protected terms still present at original frequency (or higher; never lower).
   - No claim numbers (Section 1d) drifted.
   - The section reads aloud cleanly (Section 4).
5. Run `claim_audit.py` (when implemented) before moving to the next section.
