# NeurIPS 2026 Documentation Index

Reference docs for the NeurIPS 2026 submission. Organized to mirror how the Call for Papers and Main Track Handbook flow into the broader ethics reading.

**For machine consumption:** every `.pdf` has a `.txt` sidecar from `pdftotext`. Agents should grep the `.txt` files; humans can open the `.pdf` for figures/formatting.

---

## Document flow (root → ring outward)

```
NeurIPSCallForPapers.pdf  (root: topics, dates, key links)
  └─→ MainTrackHandbook.pdf  (V2026.1: policies for Authors / Reviewers / ACs / SACs)
        ├─→ NeurIPSCodeOfConduct.pdf  (professional conduct, plagiarism, fraud)
        ├─→ NeurIPSAcademicIntegrity.pdf  (separate AI policy)
        ├─→ Reviewing Guidelines.pdf  (for reviewers)
        ├─→ OpenReview SubmissionForm.pdf  (submission portal mechanics)
        ├─→ Formatting_Instructions_For_NeurIPS_2026.zip  (LaTeX template)
        └─→ "Further reading" (ethics_reading/, organized by Code-of-Ethics concern)
```

---

## neurips_official/

Procedural docs from NeurIPS. **Read before any submission action.**

| File | Purpose |
|------|---------|
| `NeurIPSCallForPapers.pdf` | Topics list, key dates (abstract May 4, paper May 6, notification Sep 24) |
| `MainTrackHandbook.pdf` | 25-page handbook: OpenReview setup, conflicts, anti-collusion, confidentiality, Code of Ethics, Academic Integrity, paper formatting, double-blind reviewing, supplementary material rules |
| `NeurIPSCodeOfConduct.pdf` | Professional conduct, plagiarism, fraud, reproducibility |
| `NeurIPSAcademicIntegrity.pdf` | Academic integrity policy |
| `Reviewing Guidelines.pdf` | Reviewer obligations and standards |
| `OpenReview SubmissionForm.pdf` | Submission portal walkthrough |
| `Formatting_Instructions_For_NeurIPS_2026.zip` | LaTeX template (`neurips_2026.sty`); already extracted into `paper/` |

---

## ethics_reading/

Each subfolder maps to a "societal impact" concern from the Handbook's "Further reading" section. **Read when the paper or checklist needs to address that concern specifically.**

| Subfolder | Handbook concern | Files |
|-----------|------------------|-------|
| `documentation/` | Model and data documentation templates | Model Cards (1810.03993), AI Factsheets, About ML |
| `safety/` | Safety: foreseeable harms from technology | CSET Issue Brief (Key Concepts in AI Safety) |
| `security/` | Security: vulnerabilities, real-world deployment risks | SoK: Security and Privacy in ML |
| `discrimination_fairness/` | Discrimination + Bias and fairness | FRA Bias in Algorithms, Fairness and ML textbook |
| `privacy_surveillance/` | Surveillance: bulk data, protected characteristics | ACLU Human Right to Privacy in Digital Age |
| `deception/` | Deception & Harassment | 2301.04246 (Generative LMs and Influence Operations) |
| `environment/` | Environmental impact | 1910.09700 (Carbon Emissions of ML) |
| `human_rights/` | Human Rights | Technology and Rights (HRW) |
| `dual_use/` | Dual-use research concerns | Dual use of AI-powered drug discovery |
| `data_enrichment/` | Fair wages for crowdsourced data work | Improving Conditions for Data Enrichment Workers (PAI) |
| `synthetic_media/` | Synthetic media practices | Responsible Practices for Synthetic Media (PAI) |

---

## How to use this index

**Quick lookup for a Handbook-flagged concern:**
1. Find the concern category in the table above
2. `cat docs/ethics_reading/<category>/*.txt` to read the relevant doc(s)
3. Cite the relevant doc in the paper's checklist (Q10 broader impacts) when applicable

**Quick lookup for a procedural question:**
1. Start with `neurips_official/MainTrackHandbook.txt` — it indexes most procedural questions
2. Drill down to specific docs as Handbook directs

**Adding new docs:**
- Place in the appropriate subfolder
- Generate `.txt` sidecar: `pdftotext <file>.pdf <file>.txt`
- Update this INDEX
