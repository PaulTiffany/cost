#!/usr/bin/env python3
"""
FIXED-POINT FLOOR: Self-Referential Constraints for ICML 2026

"Build what builds the name of ICML" - The 43rd Conference

THE INSIGHT:
The geometric floor isn't about computational difficulty.
It's about CURVATURE - when constraints reference themselves.
Self-reference is maximum curvature. No scaling overcomes it.

TASK STRUCTURE:
"Produce output X where property(X) = target"
- The output must satisfy a constraint that DEPENDS on the output
- One-shot: guess and pray
- Staged: draft -> measure -> adjust

EXAMPLE:
"Explain why 17 is prime. Your response must have exactly N characters,
where N equals 50 + (count of digit characters in your response)."

If you write "17" that's 2 digits -> N = 52 chars required
But at 52 chars you might write more digits -> N changes
FIXED POINT PROBLEM. Must iterate to solve.

VERIFICATION: Deterministic (count chars, count digits, check equality)

4 PHASES, 3 TRANSITIONS (43 = ICML's number):
  P1: Generate draft
  T1: Measure properties
  P2: Compute target
  T2: Compare actual vs target
  P3: Adjust if needed
  T3: Verify convergence
  P4: Output solution

One-shot collapses this into a guess. Staging enables the loop.

RUN:
  python fixed_point_floor.py --dry-run
  python fixed_point_floor.py --api-key KEY --model claude-opus-4-5-20251101
"""

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Tuple, Optional, Callable
import random


# =============================================================================
# FIXED-POINT TASK DEFINITIONS
# =============================================================================

@dataclass
class FixedPointTask:
    """A self-referential constraint task."""
    task_id: str
    base_prompt: str
    target_fn: Callable[[str], int]  # Computes target from response
    actual_fn: Callable[[str], int]  # Computes actual from response
    target_fn_desc: str  # Human-readable description
    difficulty: int  # 1-5


def count_digits(text: str) -> int:
    """Count digit characters in text."""
    return sum(1 for c in text if c.isdigit())


def count_vowels(text: str) -> int:
    """Count vowels in text."""
    return sum(1 for c in text.lower() if c in 'aeiou')


def sum_digits(text: str) -> int:
    """Sum of all single digits mentioned."""
    return sum(int(c) for c in text if c.isdigit())


def count_words(text: str) -> int:
    """Count words in text."""
    return len(text.split())


def char_count(text: str) -> int:
    """Character count."""
    return len(text.strip())


# Task generators
def make_digit_sum_task(seed: int) -> FixedPointTask:
    """Target char count = base + sum of digits in response."""
    rng = random.Random(seed)
    base = rng.randint(40, 80)

    return FixedPointTask(
        task_id=f"digit_sum_{base}",
        base_prompt=f"Explain why 17 is prime. Your response must have EXACTLY N characters, where N = {base} + (sum of all digit characters in your response). Count carefully!",
        target_fn=lambda x: base + sum_digits(x),
        actual_fn=char_count,
        target_fn_desc=f"N = {base} + sum_of_digits",
        difficulty=3,
    )


def make_vowel_count_task(seed: int) -> FixedPointTask:
    """Target char count = base + 2*vowels."""
    rng = random.Random(seed)
    base = rng.randint(30, 60)

    return FixedPointTask(
        task_id=f"vowel_count_{base}",
        base_prompt=f"Explain the sum formula 1+2+...+n = n(n+1)/2. Your response must have EXACTLY N characters, where N = {base} + 2*(number of vowels in your response).",
        target_fn=lambda x: base + 2 * count_vowels(x),
        actual_fn=char_count,
        target_fn_desc=f"N = {base} + 2*vowel_count",
        difficulty=3,
    )


def make_word_digit_task(seed: int) -> FixedPointTask:
    """Target char count = words * 10 + digits * 5."""
    rng = random.Random(seed)

    return FixedPointTask(
        task_id=f"word_digit_{seed}",
        base_prompt="Explain why a^2 + b^2 = c^2 for right triangles. Your response must have EXACTLY N characters, where N = (word_count * 10) + (digit_count * 5).",
        target_fn=lambda x: count_words(x) * 10 + count_digits(x) * 5,
        actual_fn=char_count,
        target_fn_desc="N = words*10 + digits*5",
        difficulty=4,
    )


def make_self_counting_task(seed: int) -> FixedPointTask:
    """Response must state its own length correctly."""
    rng = random.Random(seed)
    topic = rng.choice(["prime numbers", "the golden ratio", "Euler's identity"])

    return FixedPointTask(
        task_id=f"self_count_{seed}",
        base_prompt=f"Write about {topic}. Your response must END with 'Length: N' where N is the exact character count of your entire response (including 'Length: N').",
        target_fn=lambda x: int(re.search(r'Length:\s*(\d+)\s*$', x).group(1)) if re.search(r'Length:\s*(\d+)\s*$', x) else -1,
        actual_fn=char_count,
        target_fn_desc="stated_length == actual_length",
        difficulty=5,
    )


# =============================================================================
# PROMPTS
# =============================================================================

def prompt_oneshot(task: FixedPointTask) -> str:
    return f"""{task.base_prompt}

Think carefully. The constraint is self-referential - what you write affects what you must write.

Your response:"""


def prompt_stage1_draft(task: FixedPointTask) -> str:
    return f"""I need to solve this self-referential constraint problem:

{task.base_prompt}

First, write a DRAFT response that explains the topic well. Don't worry about the length constraint yet - just write good content. I'll measure and adjust in the next step.

Draft:"""


def prompt_stage2_measure_adjust(task: FixedPointTask, draft: str, target: int, actual: int) -> str:
    diff = actual - target
    direction = "too long" if diff > 0 else "too short"

    return f"""My draft has {actual} characters but needs EXACTLY {target} characters ({direction} by {abs(diff)}).

The constraint: {task.target_fn_desc}

My draft:
{draft}

Adjust this draft to have EXACTLY {target} characters while keeping the explanation clear. Output ONLY the adjusted response, nothing else:"""


def prompt_stage3_verify_adjust(task: FixedPointTask, attempt: str, target: int, actual: int) -> str:
    if actual == target:
        return None  # No adjustment needed

    diff = actual - target
    direction = "remove" if diff > 0 else "add"

    return f"""Still not exact. Current: {actual} chars, Need: {target} chars.

{direction.upper()} exactly {abs(diff)} characters while keeping it coherent.

Current text:
{attempt}

Final adjusted version (EXACTLY {target} chars):"""


# =============================================================================
# VERIFICATION
# =============================================================================

def verify_fixed_point(text: str, task: FixedPointTask) -> dict:
    """Check if response satisfies the fixed-point constraint."""
    text = text.strip()

    try:
        target = task.target_fn(text)
        actual = task.actual_fn(text)

        return {
            "target": target,
            "actual": actual,
            "delta": actual - target,
            "is_fixed_point": actual == target,
            "char_count": len(text),
        }
    except Exception as e:
        return {
            "target": -1,
            "actual": len(text),
            "delta": float('inf'),
            "is_fixed_point": False,
            "error": str(e),
        }


# =============================================================================
# API CLIENT
# =============================================================================

class BudgetExceeded(Exception):
    """Raised when budget guards are hit."""
    pass


class APIClient:
    def __init__(self, api_key: str, max_calls: int = 50, max_cost: float = 2.0):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.max_calls = max_calls
        self.max_cost = max_cost

    def estimated_cost(self) -> float:
        """Estimate cost (Opus pricing)."""
        return (self.input_tokens / 1e6 * 15) + (self.output_tokens / 1e6 * 75)

    def check_budget(self):
        """Raise if budget guards exceeded."""
        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"Max calls ({self.max_calls}) reached. Cost: ${self.estimated_cost():.2f}")
        if self.estimated_cost() >= self.max_cost:
            raise BudgetExceeded(f"Max cost (${self.max_cost:.2f}) reached after {self.calls} calls")

    def generate(self, model_id: str, prompt: str, max_tokens: int) -> str:
        self.check_budget()
        try:
            r = self.client.messages.create(
                model=model_id,
                max_tokens=max_tokens,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}]
            )
            self.calls += 1
            self.input_tokens += r.usage.input_tokens
            self.output_tokens += r.usage.output_tokens
            return r.content[0].text.strip()
        except BudgetExceeded:
            raise
        except Exception as e:
            print(f"    API ERROR: {e}")
            return ""


# =============================================================================
# EXPERIMENT
# =============================================================================

@dataclass
class TrialResult:
    task_id: str
    model_id: str
    protocol: str
    is_fixed_point: bool
    target: int
    actual: int
    delta: int
    iterations: int
    response_preview: str


def run_oneshot(client: APIClient, model_id: str, task: FixedPointTask, budget: int) -> TrialResult:
    """One-shot attempt at fixed-point task."""
    response = client.generate(model_id, prompt_oneshot(task), budget)
    v = verify_fixed_point(response, task)

    return TrialResult(
        task_id=task.task_id,
        model_id=model_id,
        protocol="oneshot",
        is_fixed_point=v["is_fixed_point"],
        target=v["target"],
        actual=v["actual"],
        delta=v["delta"],
        iterations=1,
        response_preview=response[:60] + "..." if len(response) > 60 else response,
    )


def run_staged(client: APIClient, model_id: str, task: FixedPointTask, budget: int, max_iterations: int = 3) -> TrialResult:
    """Staged attempt with measure-adjust loop."""
    # Budget split: draft gets half, adjustments share the rest
    draft_budget = budget // 2
    adjust_budget = (budget - draft_budget) // max_iterations

    # Stage 1: Draft
    draft = client.generate(model_id, prompt_stage1_draft(task), draft_budget)
    v = verify_fixed_point(draft, task)

    current = draft
    iterations = 1

    # Stages 2-4: Measure and adjust loop
    for i in range(max_iterations):
        if v["is_fixed_point"]:
            break

        iterations += 1
        target = v["target"]
        actual = v["actual"]

        if i == 0:
            prompt = prompt_stage2_measure_adjust(task, current, target, actual)
        else:
            prompt = prompt_stage3_verify_adjust(task, current, target, actual)

        if prompt is None:
            break

        current = client.generate(model_id, prompt, adjust_budget)
        v = verify_fixed_point(current, task)

    return TrialResult(
        task_id=task.task_id,
        model_id=model_id,
        protocol="staged",
        is_fixed_point=v["is_fixed_point"],
        target=v["target"],
        actual=v["actual"],
        delta=v["delta"],
        iterations=iterations,
        response_preview=current[:60] + "..." if len(current) > 60 else current,
    )


def run_experiment(api_key: str, model_id: str, seed: int, budget: int = 512,
                   max_calls: int = 50, max_cost: float = 2.0):
    """Run fixed-point floor experiment with budget guards."""

    client = APIClient(api_key, max_calls=max_calls, max_cost=max_cost)

    # Generate task suite
    tasks = [
        make_digit_sum_task(seed),
        make_digit_sum_task(seed + 1),
        make_vowel_count_task(seed),
        make_vowel_count_task(seed + 1),
        make_word_digit_task(seed),
        make_self_counting_task(seed),
    ]

    print()
    print("=" * 70)
    print("  FIXED-POINT FLOOR EXPERIMENT")
    print("  ICML 2026 - The 43rd Conference")
    print("=" * 70)
    print()
    print(f"  Model: {model_id}")
    print(f"  Seed: {seed}")
    print(f"  Budget: {budget} tokens")
    print(f"  Tasks: {len(tasks)}")
    print()
    print("  4 Phases, 3 Transitions:")
    print("    P1: Generate -> T1: Measure -> P2: Compute target")
    print("    T2: Compare -> P3: Adjust -> T3: Verify -> P4: Output")
    print()

    results = []
    floors_found = 0
    budget_exceeded = False

    print(f"  {'Task':<20} | {'One-Shot':<20} | {'Staged':<20} | {'Result':<10}")
    print("  " + "-" * 75)

    try:
        for task in tasks:
            os_result = run_oneshot(client, model_id, task, budget)
            st_result = run_staged(client, model_id, task, budget)

            results.append(asdict(os_result))
            results.append(asdict(st_result))

            os_str = f"{'PASS' if os_result.is_fixed_point else 'FAIL'} (d={os_result.delta:+d})"
            st_str = f"{'PASS' if st_result.is_fixed_point else 'FAIL'} (d={st_result.delta:+d}, i={st_result.iterations})"

            if st_result.is_fixed_point and not os_result.is_fixed_point:
                finding = "FLOOR"
                floors_found += 1
            elif os_result.is_fixed_point and not st_result.is_fixed_point:
                finding = "ceiling"
            elif os_result.is_fixed_point and st_result.is_fixed_point:
                finding = "both ok"
            else:
                finding = "both fail"

            print(f"  {task.task_id:<20} | {os_str:<20} | {st_str:<20} | {finding:<10}")
            print(f"  [Budget: {client.calls}/{client.max_calls} calls, ${client.estimated_cost():.2f}/${client.max_cost:.2f}]")

    except BudgetExceeded as e:
        print(f"\n  *** BUDGET GUARD TRIGGERED ***")
        print(f"  {e}")
        budget_exceeded = True

    print()
    print("=" * 70)
    print(f"  FLOORS FOUND: {floors_found}/{len(tasks)}")
    print("=" * 70)

    # Save results
    output = {
        "meta": {
            "experiment": "fixed_point_floor",
            "model": model_id,
            "seed": seed,
            "budget": budget,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "api_calls": client.calls,
            "input_tokens": client.input_tokens,
            "output_tokens": client.output_tokens,
        },
        "summary": {
            "floors_found": floors_found,
            "total_tasks": len(tasks),
        },
        "results": results,
    }

    output_path = f"fixed_point_floor_{model_id.split('-')[1]}_{seed}.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    cost = (client.input_tokens / 1e6 * 15) + (client.output_tokens / 1e6 * 75)
    print(f"\n  Results: {output_path}")
    print(f"  API calls: {client.calls}, Est. cost: ${cost:.2f}")

    return output


def dry_run(seed: int = 43):
    """Show what would be tested."""
    tasks = [
        make_digit_sum_task(seed),
        make_vowel_count_task(seed),
        make_word_digit_task(seed),
        make_self_counting_task(seed),
    ]

    print()
    print("=" * 70)
    print("  FIXED-POINT FLOOR - DRY RUN")
    print("  ICML 2026 - The 43rd Conference (4 phases, 3 transitions)")
    print("=" * 70)
    print()
    print("  THE INSIGHT:")
    print("  Self-referential constraints have MAXIMUM curvature.")
    print("  The output affects the constraint on the output.")
    print("  No amount of scaling makes this trivial - you must SEARCH.")
    print()
    print("  TASKS:")
    for t in tasks:
        print(f"\n    [{t.task_id}]")
        print(f"    {t.base_prompt[:70]}...")
        print(f"    Constraint: {t.target_fn_desc}")
    print()
    print("  WHY THIS FINDS THE FLOOR:")
    print("    One-shot: Guess the fixed point (unlikely)")
    print("    Staged: Draft -> Measure -> Adjust -> Verify (can converge)")
    print()
    print("  Expected: ~12 API calls per model")
    print("  Estimated cost: ~$0.50 per model")
    print()


def main():
    parser = argparse.ArgumentParser(description="Fixed-Point Floor - ICML 2026")
    parser.add_argument("--api-key", help="Anthropic API key")
    parser.add_argument("--model", default="claude-opus-4-5-20251101", help="Model to test")
    parser.add_argument("--seed", type=int, default=43, help="RNG seed (43 = ICML's number)")
    parser.add_argument("--budget", type=int, default=512, help="Token budget")
    parser.add_argument("--dry-run", action="store_true")
    # BUDGET GUARDS
    parser.add_argument("--max-calls", type=int, default=50, help="Max API calls (default 50)")
    parser.add_argument("--max-cost", type=float, default=2.0, help="Max cost in $ (default 2.0)")
    args = parser.parse_args()

    if args.dry_run:
        dry_run(args.seed)
        return

    if not args.api_key:
        sys.exit("ERROR: --api-key required")

    run_experiment(args.api_key, args.model, args.seed, args.budget,
                   max_calls=args.max_calls, max_cost=args.max_cost)


if __name__ == "__main__":
    main()
