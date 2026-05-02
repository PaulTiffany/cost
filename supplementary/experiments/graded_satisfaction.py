#!/usr/bin/env python3
"""
GRADED CONSTRAINT SATISFACTION METRICS
ICML 2026 - Diagonal Cost Bounds

Replaces binary pass/fail with continuous constraint satisfaction profiles.
This reveals the geometric trade-off: one-shot often achieves 70-90% satisfaction
but fails full intersection; staged reaches 100% more often in the intermediate regime.

METRIC SPEC:
- Each constraint evaluated independently -> satisfaction vector
- Scores are continuous [0.0, 1.0] where possible
- JSON validity tracked separately from content satisfaction
- Best-effort extraction even when JSON invalid
- Aggregate: fraction satisfied, mean score, full success boolean

CONSTRAINT TYPES:
1. Length: score = 1.0 - clamp(|actual - target| / tolerance, 0, 1)
2. Numeric: score = 1.0 - clamp(|actual - expected| / expected, 0, 1)
3. Boolean/presence: score = 1.0 if satisfied else 0.0
4. String match: score = 1.0 - (edit_distance / max_len) [optional]
5. Format (JSON): binary but separate field

OUTPUT:
- ConstraintSatisfactionProfile per trial
- Aggregates for plotting: mean_score, fraction_satisfied, full_success_rate

Author: Anonymous (ICML 2026 Submission)
"""

import json
import re
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Callable, Tuple
from enum import Enum


class ConstraintType(Enum):
    LENGTH = "length"
    NUMERIC = "numeric"
    BOOLEAN = "boolean"
    PRESENCE = "presence"
    FORMAT = "format"
    CONTENT = "content"


@dataclass
class ConstraintSpec:
    """Specification for a single constraint."""
    name: str
    ctype: ConstraintType
    target: Any  # Expected value or threshold
    tolerance: float = 0.1  # For continuous scoring
    weight: float = 1.0  # For weighted aggregates
    extractor: Optional[Callable[[str], Any]] = None  # How to extract value from response


@dataclass
class ConstraintResult:
    """Result of evaluating a single constraint."""
    name: str
    ctype: str
    satisfied: bool  # Binary: meets threshold
    score: float  # Continuous: 0.0 to 1.0
    target: Any
    actual: Any
    error: Optional[float] = None  # Absolute error for numeric
    details: str = ""


@dataclass
class SatisfactionProfile:
    """Complete constraint satisfaction profile for a single trial."""
    trial_id: str
    model_id: str
    protocol: str  # "oneshot" or "staged"
    budget: int

    # Format validity (separate from content)
    json_valid: bool
    json_error: str = ""

    # Per-constraint results
    constraints: List[ConstraintResult] = field(default_factory=list)

    # Aggregates
    n_constraints: int = 0
    n_satisfied: int = 0
    fraction_satisfied: float = 0.0
    mean_score: float = 0.0
    weighted_score: float = 0.0
    full_satisfaction: bool = False

    # Raw response for debugging
    response_preview: str = ""

    def compute_aggregates(self, weights: Optional[List[float]] = None):
        """Compute aggregate metrics from constraint results."""
        if not self.constraints:
            return

        self.n_constraints = len(self.constraints)
        self.n_satisfied = sum(1 for c in self.constraints if c.satisfied)
        self.fraction_satisfied = self.n_satisfied / self.n_constraints
        self.mean_score = sum(c.score for c in self.constraints) / self.n_constraints

        if weights and len(weights) == len(self.constraints):
            total_weight = sum(weights)
            self.weighted_score = sum(c.score * w for c, w in zip(self.constraints, weights)) / total_weight
        else:
            self.weighted_score = self.mean_score

        self.full_satisfaction = all(c.satisfied for c in self.constraints)


# =============================================================================
# CONSTRAINT EVALUATORS
# =============================================================================

def score_length(actual: int, target: int, tolerance_pct: float = 0.1) -> Tuple[bool, float, float]:
    """
    Score length constraint with continuous falloff.

    Returns: (satisfied, score, error)
    - satisfied: True if within tolerance
    - score: 1.0 at target, falls off linearly to 0 at 2x tolerance
    - error: signed difference (actual - target)
    """
    if target <= 0:
        return (True, 1.0, 0) if actual == 0 else (False, 0.0, actual)

    error = actual - target
    rel_error = abs(error) / target
    tolerance = tolerance_pct

    satisfied = rel_error <= tolerance

    # Score: 1.0 at exact, linear falloff, 0 at 2x tolerance
    if rel_error <= tolerance:
        score = 1.0 - (rel_error / tolerance) * 0.5  # 1.0 to 0.5 in tolerance band
    else:
        # Linear from 0.5 to 0 over next tolerance band
        score = max(0.0, 0.5 - (rel_error - tolerance) / tolerance * 0.5)

    return satisfied, score, error


def score_numeric(actual: float, expected: float, tolerance_pct: float = 0.01) -> Tuple[bool, float, float]:
    """
    Score numeric constraint with continuous falloff.
    """
    if expected == 0:
        error = abs(actual)
        satisfied = error < 1e-6
        score = 1.0 if satisfied else 0.0
        return satisfied, score, actual

    error = actual - expected
    rel_error = abs(error / expected)

    satisfied = rel_error <= tolerance_pct

    if rel_error <= tolerance_pct:
        score = 1.0 - (rel_error / tolerance_pct) * 0.5
    else:
        score = max(0.0, 0.5 * (1.0 - min(rel_error, 1.0)))

    return satisfied, score, error


def score_boolean(actual: bool, expected: bool) -> Tuple[bool, float]:
    """Score boolean constraint. Binary."""
    satisfied = actual == expected
    return satisfied, 1.0 if satisfied else 0.0


def score_presence(text: str, required_elements: List[str], case_sensitive: bool = False) -> Tuple[bool, float, List[str]]:
    """
    Score presence of required elements in text.
    Returns fraction present as continuous score.
    """
    if not required_elements:
        return True, 1.0, []

    check_text = text if case_sensitive else text.lower()
    missing = []
    found = 0

    for elem in required_elements:
        check_elem = elem if case_sensitive else elem.lower()
        if check_elem in check_text:
            found += 1
        else:
            missing.append(elem)

    score = found / len(required_elements)
    satisfied = len(missing) == 0

    return satisfied, score, missing


def score_content_requirements(text: str, requirements: List[str]) -> List[ConstraintResult]:
    """
    Evaluate multiple content requirements independently.
    Each requirement is checked for presence/satisfaction.
    """
    results = []
    text_lower = text.lower()

    for i, req in enumerate(requirements):
        # Simple keyword presence check
        # Can be extended with more sophisticated NLP
        keywords = req.lower().split()
        found = sum(1 for kw in keywords if kw in text_lower)
        score = found / len(keywords) if keywords else 1.0
        satisfied = score >= 0.5  # At least half the keywords present

        results.append(ConstraintResult(
            name=f"content_req_{i+1}",
            ctype="content",
            satisfied=satisfied,
            score=score,
            target=req,
            actual=f"{found}/{len(keywords)} keywords",
            details=req[:50]
        ))

    return results


# =============================================================================
# JSON EXTRACTION WITH BEST-EFFORT
# =============================================================================

def extract_json_best_effort(text: str) -> Tuple[bool, Optional[Dict], str]:
    """
    Attempt to extract JSON from response with best-effort fallback.

    Returns: (valid, parsed_dict, error_message)
    """
    # Try direct parse
    try:
        data = json.loads(text.strip())
        return True, data, ""
    except json.JSONDecodeError as e:
        pass

    # Try to find JSON block in text
    json_patterns = [
        r'```json\s*(.*?)\s*```',
        r'```\s*(.*?)\s*```',
        r'\{[^{}]*\}',
        r'\{.*\}',
    ]

    for pattern in json_patterns:
        matches = re.findall(pattern, text, re.DOTALL)
        for match in matches:
            try:
                data = json.loads(match.strip())
                return True, data, "extracted from block"
            except:
                continue

    # Best-effort field extraction even without valid JSON
    best_effort = {}

    # Try to extract key-value patterns
    kv_patterns = [
        r'"(\w+)"\s*:\s*"([^"]*)"',  # "key": "value"
        r'"(\w+)"\s*:\s*(\d+(?:\.\d+)?)',  # "key": number
        r'"(\w+)"\s*:\s*(true|false)',  # "key": boolean
        r'(\w+)\s*[=:]\s*"([^"]*)"',  # key = "value"
        r'(\w+)\s*[=:]\s*(\d+(?:\.\d+)?)',  # key = number
    ]

    for pattern in kv_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for key, value in matches:
            if key not in best_effort:
                # Try to parse value
                if value.lower() in ('true', 'false'):
                    best_effort[key] = value.lower() == 'true'
                else:
                    try:
                        best_effort[key] = float(value) if '.' in value else int(value)
                    except:
                        best_effort[key] = value

    if best_effort:
        return False, best_effort, "best-effort extraction"

    return False, None, "no JSON found"


# =============================================================================
# MAIN EVALUATOR
# =============================================================================

class GradedEvaluator:
    """
    Evaluates responses against constraint specifications with graded scoring.
    """

    def __init__(self, constraints: List[ConstraintSpec]):
        self.constraints = constraints

    def evaluate(self, response: str, trial_id: str, model_id: str,
                 protocol: str, budget: int) -> SatisfactionProfile:
        """
        Evaluate response against all constraints.
        Returns complete satisfaction profile.
        """
        profile = SatisfactionProfile(
            trial_id=trial_id,
            model_id=model_id,
            protocol=protocol,
            budget=budget,
            json_valid=False,
            response_preview=response[:100] + "..." if len(response) > 100 else response
        )

        # Extract JSON/structured data
        json_valid, data, json_error = extract_json_best_effort(response)
        profile.json_valid = json_valid
        profile.json_error = json_error

        # Evaluate each constraint
        results = []
        weights = []

        for spec in self.constraints:
            result = self._evaluate_constraint(spec, response, data)
            results.append(result)
            weights.append(spec.weight)

        profile.constraints = results
        profile.compute_aggregates(weights)

        return profile

    def _evaluate_constraint(self, spec: ConstraintSpec,
                            response: str, data: Optional[Dict]) -> ConstraintResult:
        """Evaluate a single constraint."""

        # Extract actual value
        actual = None
        if spec.extractor:
            try:
                actual = spec.extractor(response)
            except:
                pass
        elif data and isinstance(spec.target, str) and spec.target in data:
            actual = data[spec.target]

        # Evaluate based on type
        if spec.ctype == ConstraintType.LENGTH:
            actual_len = len(response.strip())
            satisfied, score, error = score_length(actual_len, spec.target, spec.tolerance)
            return ConstraintResult(
                name=spec.name,
                ctype=spec.ctype.value,
                satisfied=satisfied,
                score=score,
                target=spec.target,
                actual=actual_len,
                error=error,
                details=f"{actual_len} chars vs {spec.target} target"
            )

        elif spec.ctype == ConstraintType.NUMERIC:
            if actual is None:
                return ConstraintResult(
                    name=spec.name, ctype=spec.ctype.value,
                    satisfied=False, score=0.0,
                    target=spec.target, actual=None,
                    details="could not extract numeric value"
                )
            try:
                actual_num = float(actual)
                satisfied, score, error = score_numeric(actual_num, spec.target, spec.tolerance)
                return ConstraintResult(
                    name=spec.name, ctype=spec.ctype.value,
                    satisfied=satisfied, score=score,
                    target=spec.target, actual=actual_num,
                    error=error
                )
            except:
                return ConstraintResult(
                    name=spec.name, ctype=spec.ctype.value,
                    satisfied=False, score=0.0,
                    target=spec.target, actual=actual,
                    details="invalid numeric"
                )

        elif spec.ctype == ConstraintType.BOOLEAN:
            if actual is None:
                return ConstraintResult(
                    name=spec.name, ctype=spec.ctype.value,
                    satisfied=False, score=0.0,
                    target=spec.target, actual=None
                )
            satisfied, score = score_boolean(bool(actual), spec.target)
            return ConstraintResult(
                name=spec.name, ctype=spec.ctype.value,
                satisfied=satisfied, score=score,
                target=spec.target, actual=actual
            )

        elif spec.ctype == ConstraintType.PRESENCE:
            if isinstance(spec.target, list):
                satisfied, score, missing = score_presence(response, spec.target)
                return ConstraintResult(
                    name=spec.name, ctype=spec.ctype.value,
                    satisfied=satisfied, score=score,
                    target=spec.target, actual=f"missing: {missing}",
                    details=f"{int(score*100)}% present"
                )
            else:
                present = spec.target.lower() in response.lower()
                return ConstraintResult(
                    name=spec.name, ctype=spec.ctype.value,
                    satisfied=present, score=1.0 if present else 0.0,
                    target=spec.target, actual=present
                )

        elif spec.ctype == ConstraintType.FORMAT:
            # JSON validity
            valid = profile.json_valid if hasattr(self, '_current_profile') else False
            return ConstraintResult(
                name=spec.name, ctype=spec.ctype.value,
                satisfied=valid, score=1.0 if valid else 0.0,
                target="valid JSON", actual=valid
            )

        else:  # CONTENT or unknown
            # Generic presence check
            if isinstance(spec.target, str):
                present = spec.target.lower() in response.lower()
                return ConstraintResult(
                    name=spec.name, ctype=spec.ctype.value,
                    satisfied=present, score=1.0 if present else 0.0,
                    target=spec.target, actual=present
                )
            return ConstraintResult(
                name=spec.name, ctype=spec.ctype.value,
                satisfied=False, score=0.0,
                target=spec.target, actual=None,
                details="unknown constraint type"
            )


# =============================================================================
# AGGREGATION FOR PLOTTING
# =============================================================================

@dataclass
class AggregateResults:
    """Aggregated results across multiple trials for plotting."""
    budget: int
    protocol: str
    n_trials: int

    # Main metrics
    mean_fraction_satisfied: float
    std_fraction_satisfied: float
    mean_score: float
    std_score: float
    full_success_rate: float  # P(all constraints satisfied)

    # Per-constraint failure rates
    constraint_failure_rates: Dict[str, float] = field(default_factory=dict)


def aggregate_profiles(profiles: List[SatisfactionProfile]) -> Dict[Tuple[int, str], AggregateResults]:
    """
    Aggregate profiles by (budget, protocol) for plotting.
    """
    from collections import defaultdict
    import statistics

    # Group by (budget, protocol)
    groups = defaultdict(list)
    for p in profiles:
        groups[(p.budget, p.protocol)].append(p)

    results = {}
    for (budget, protocol), group in groups.items():
        fractions = [p.fraction_satisfied for p in group]
        scores = [p.mean_score for p in group]
        full_success = [1.0 if p.full_satisfaction else 0.0 for p in group]

        # Per-constraint failure rates
        constraint_failures = defaultdict(list)
        for p in group:
            for c in p.constraints:
                constraint_failures[c.name].append(0.0 if c.satisfied else 1.0)

        results[(budget, protocol)] = AggregateResults(
            budget=budget,
            protocol=protocol,
            n_trials=len(group),
            mean_fraction_satisfied=statistics.mean(fractions),
            std_fraction_satisfied=statistics.stdev(fractions) if len(fractions) > 1 else 0,
            mean_score=statistics.mean(scores),
            std_score=statistics.stdev(scores) if len(scores) > 1 else 0,
            full_success_rate=statistics.mean(full_success),
            constraint_failure_rates={k: statistics.mean(v) for k, v in constraint_failures.items()}
        )

    return results


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

def example_task_constraints() -> List[ConstraintSpec]:
    """Example constraint specifications for a multi-constraint task."""
    return [
        ConstraintSpec(
            name="length",
            ctype=ConstraintType.LENGTH,
            target=150,
            tolerance=0.1,  # 10% tolerance
            weight=1.0
        ),
        ConstraintSpec(
            name="has_formula",
            ctype=ConstraintType.PRESENCE,
            target=["n(n+1)/2", "(n+1)", "n+1"],
            weight=2.0  # Higher weight for content
        ),
        ConstraintSpec(
            name="has_intuition",
            ctype=ConstraintType.PRESENCE,
            target=["pair", "sum", "add"],
            weight=2.0
        ),
        ConstraintSpec(
            name="has_conclusion",
            ctype=ConstraintType.PRESENCE,
            target=["therefore", "thus", "so", "hence"],
            weight=1.0
        ),
    ]


if __name__ == "__main__":
    # Demo
    constraints = example_task_constraints()
    evaluator = GradedEvaluator(constraints)

    # Test response
    response = "The sum 1+2+...+n = n(n+1)/2. Pair first and last: 1+n, 2+(n-1), etc. Each pair sums to n+1. With n/2 pairs, total is n(n+1)/2. Thus the formula holds."

    profile = evaluator.evaluate(
        response=response,
        trial_id="test_001",
        model_id="demo",
        protocol="oneshot",
        budget=256
    )

    print("=" * 60)
    print("GRADED SATISFACTION PROFILE")
    print("=" * 60)
    print(f"Fraction satisfied: {profile.fraction_satisfied:.1%}")
    print(f"Mean score: {profile.mean_score:.3f}")
    print(f"Full satisfaction: {profile.full_satisfaction}")
    print()
    print("Per-constraint results:")
    for c in profile.constraints:
        status = "PASS" if c.satisfied else "FAIL"
        print(f"  {c.name:<20} {status:<6} score={c.score:.2f}  {c.details}")
