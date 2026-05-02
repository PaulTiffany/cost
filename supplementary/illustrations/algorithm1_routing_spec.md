```markdown
### TITLE
Regime-Based Routing Decision Process

### LAYOUT
- Start Node: "Input"
  - Arrows to "Parse Constraints"
- Node: "Parse Constraints"
  - Arrows to "Compute Regime Index" and "Estimate Budget"
- Node: "Compute Regime Index"
  - Arrows to "Compute Screened Cost"
- Node: "Estimate Budget"
  - Arrows to "Compute Screened Cost"
- Node: "Compute Screened Cost"
  - Arrows to Decision Node
- Decision Node: "Route Decision"
  - Arrows to "One-Shot", "Staged", and "Decompose-First"

### KEY ELEMENTS
- "Input": Represents the initial inputs: Prompt $P$, token budget $T$, encoder $\Phi$.
- "Parse Constraints": Extracts constraints $C_1, \ldots, C_k$ from $P$.
- "Compute Regime Index": Calculates $\hat{\rho}$ using cosine similarity.
- "Estimate Budget": Computes $\delta$.
- "Compute Screened Cost": Calculates $\widehat{\delta}_{\min}$.
- "Route Decision": Determines the routing strategy based on conditions.

### EMPHASIS
- Highlight the conditions leading to each routing decision in the "Route Decision" node.

### OMIT
- Do not depict the detailed mathematical formulas or intermediate variables like $\hat{\rho}_{ij}$.

### CLAIM SCOPE
- The figure illustrates the decision process for routing strategies based on input analysis.
- It does not claim to show implementation details or performance outcomes.
```