#!/usr/bin/env python3
"""
CLAUDE GENOME ANALYSIS
ICML 2026 - Diagonal Cost Bounds

Proper analysis of the Claude genome experiment data:
1. Map observed delta_min for each constraint and their combination
2. Estimate implied rho from delta_min ratios
3. Test dependency hypothesis (Sonnet anomaly)
4. Compare to theoretical predictions
"""

import json
import math
from pathlib import Path


def load_genome_data(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def theoretical_delta_min(delta_single: float, rho: float, k: int = 2) -> float:
    """
    Compute theoretical delta_min for k constraints with conflict rho.

    For k=2: delta_min = delta_single × √(2/(1-rho))
    """
    if rho >= 1.0:
        return float('inf')
    return delta_single * math.sqrt(2.0 / (1.0 - rho))


def implied_rho(delta_single: float, delta_both: float) -> float:
    """
    Invert the formula to get implied rho from observed delta_min values.

    delta_both = delta_single × √(2/(1-rho))
    -> (delta_both/delta_single)² = 2/(1-rho)
    -> 1-rho = 2/(delta_both/delta_single)²
    -> rho = 1 - 2×(delta_single/delta_both)²
    """
    if delta_both is None or delta_both == float('inf'):
        return 1.0  # Perfect conflict

    ratio = delta_both / delta_single
    if ratio <= math.sqrt(2):
        return 0.0  # No amplification needed

    rho = 1.0 - 2.0 * (delta_single / delta_both) ** 2
    return max(0.0, min(1.0, rho))


def analyze_constraint_independence(probes: list) -> dict:
    """
    Test if constraints are independent by checking P(both) vs P(a)×P(b).

    If independent: P(both) = P(a) × P(b)
    Deviation indicates dependency or conflict.
    """
    results = []
    for probe in probes:
        budget = probe['budget']
        for protocol in ['oneshot', 'staged']:
            data = probe[protocol]
            p_a = data['pass_a']
            p_b = data['pass_b']
            p_both = data['pass_both']

            # Expected if independent
            p_expected = p_a * p_b

            # Deviation
            if p_expected > 0:
                ratio = p_both / p_expected
            elif p_both == 0:
                ratio = 1.0  # Both zero, technically "matches"
            else:
                ratio = float('inf')

            results.append({
                'budget': budget,
                'protocol': protocol,
                'p_a': p_a,
                'p_b': p_b,
                'p_both': p_both,
                'p_expected': p_expected,
                'independence_ratio': ratio
            })

    return results


def find_delta_min(probes: list, metric: str, protocol: str, threshold: float = 0.5) -> int:
    """Find minimum delta where pass rate exceeds threshold."""
    for probe in probes:
        if probe[protocol][metric] >= threshold:
            return probe['budget']
    return None


def analyze_model(model_data: dict) -> dict:
    """Full analysis for a single model."""
    name = model_data['model_name']
    probes = model_data['probes']

    # Find delta_min for each constraint and combination
    delta_a_oneshot = find_delta_min(probes, 'pass_a', 'oneshot')
    delta_b_oneshot = find_delta_min(probes, 'pass_b', 'oneshot')
    delta_both_oneshot = find_delta_min(probes, 'pass_both', 'oneshot')

    delta_a_staged = find_delta_min(probes, 'pass_a', 'staged')
    delta_b_staged = find_delta_min(probes, 'pass_b', 'staged')
    delta_both_staged = find_delta_min(probes, 'pass_both', 'staged')

    # Compute implied rho for one-shot
    if delta_a_oneshot and delta_both_oneshot:
        rho_implied = implied_rho(delta_a_oneshot, delta_both_oneshot)
    elif delta_a_oneshot and delta_both_oneshot is None:
        rho_implied = 1.0  # Never succeeds -> perfect conflict
    else:
        rho_implied = None

    # Theoretical prediction: what delta_both should be for various rho
    if delta_a_oneshot:
        theoretical_predictions = {
            'rho_0.0': theoretical_delta_min(delta_a_oneshot, 0.0),
            'rho_0.3': theoretical_delta_min(delta_a_oneshot, 0.3),
            'rho_0.5': theoretical_delta_min(delta_a_oneshot, 0.5),
            'rho_0.7': theoretical_delta_min(delta_a_oneshot, 0.7),
            'rho_0.9': theoretical_delta_min(delta_a_oneshot, 0.9),
        }
    else:
        theoretical_predictions = {}

    # Independence analysis
    independence = analyze_constraint_independence(probes)

    # Staging benefit
    staging_benefit = None
    if delta_both_oneshot is None and delta_both_staged:
        staging_benefit = "REQUIRED"
    elif delta_both_oneshot and delta_both_staged:
        if delta_both_staged < delta_both_oneshot:
            staging_benefit = f"HELPS ({delta_both_oneshot} -> {delta_both_staged})"
        else:
            staging_benefit = "NOT NEEDED"

    return {
        'model': name,
        'delta_min': {
            'oneshot': {
                'pass_a': delta_a_oneshot,
                'pass_b': delta_b_oneshot,
                'pass_both': delta_both_oneshot,
            },
            'staged': {
                'pass_a': delta_a_staged,
                'pass_b': delta_b_staged,
                'pass_both': delta_both_staged,
            }
        },
        'rho_implied': rho_implied,
        'theoretical_predictions': theoretical_predictions,
        'staging_benefit': staging_benefit,
        'independence_analysis': independence,
    }


def print_analysis(analysis: dict):
    """Print formatted analysis."""
    print(f"\n{'='*70}")
    print(f"  MODEL: {analysis['model']}")
    print(f"{'='*70}")

    print(f"\n  delta_min values:")
    print(f"  {'Metric':<15} {'One-shot':>10} {'Staged':>10}")
    print(f"  {'-'*35}")

    for metric in ['pass_a', 'pass_b', 'pass_both']:
        os_val = analysis['delta_min']['oneshot'][metric]
        st_val = analysis['delta_min']['staged'][metric]
        os_str = str(os_val) if os_val else "NEVER"
        st_str = str(st_val) if st_val else "NEVER"
        print(f"  {metric:<15} {os_str:>10} {st_str:>10}")

    print(f"\n  Implied conflict (rho): ", end="")
    if analysis['rho_implied'] is not None:
        if analysis['rho_implied'] >= 1.0:
            print("INF (NEVER succeeds one-shot)")
        else:
            print(f"{analysis['rho_implied']:.2f}")
    else:
        print("N/A")

    if analysis['theoretical_predictions']:
        print(f"\n  Theoretical delta_min(both) for delta_a = {analysis['delta_min']['oneshot']['pass_a']}:")
        for rho_key, val in analysis['theoretical_predictions'].items():
            rho = rho_key.replace('rho_', 'rho=')
            print(f"    {rho}: {val:.1f}")

    print(f"\n  Staging benefit: {analysis['staging_benefit']}")

    # Check for the Sonnet anomaly pattern
    os_a = analysis['delta_min']['oneshot']['pass_a']
    os_b = analysis['delta_min']['oneshot']['pass_b']
    os_both = analysis['delta_min']['oneshot']['pass_both']

    if os_a and os_b is None and os_both is None:
        print(f"\n  **  ANOMALY DETECTED: Passes A, NEVER passes B one-shot")
        print(f"      This suggests format interference, not simple rho conflict")
    elif os_a and os_b and os_both is None:
        print(f"\n  **  ANOMALY: Passes A and B separately but NEVER both")
        print(f"      This suggests constraint dependency/interference")


def sonnet_deep_dive(genome_data: dict):
    """Deep analysis of the Sonnet 4.5 anomaly."""
    print("\n" + "="*70)
    print("  SONNET 4.5 DEEP DIVE: THE ANOMALY")
    print("="*70)

    sonnet = None
    for model in genome_data['results']:
        if '4.5-sonnet' in model['model_name']:
            sonnet = model
            break

    if not sonnet:
        print("  Sonnet data not found")
        return

    print("\n  One-shot behavior across all budgets:")
    print(f"  {'Budget':<8} {'pass_a':>10} {'pass_b':>10} {'pass_both':>10}")
    print(f"  {'-'*40}")

    for probe in sonnet['probes']:
        b = probe['budget']
        os = probe['oneshot']
        print(f"  {b:<8} {os['pass_a']:>10.1%} {os['pass_b']:>10.1%} {os['pass_both']:>10.1%}")

    print("\n  OBSERVATION:")
    print("  - Sonnet achieves 100% correctness (pass_a) at delta>=96")
    print("  - But NEVER achieves format compliance (pass_b) one-shot")
    print("  - Even at delta=512 (5x the correctness threshold)")

    print("\n  Staged behavior:")
    print(f"  {'Budget':<8} {'pass_a':>10} {'pass_b':>10} {'pass_both':>10}")
    print(f"  {'-'*40}")

    for probe in sonnet['probes']:
        b = probe['budget']
        st = probe['staged']
        print(f"  {b:<8} {st['pass_a']:>10.1%} {st['pass_b']:>10.1%} {st['pass_both']:>10.1%}")

    print("\n  OBSERVATION:")
    print("  - Staged achieves 100% on both at delta>=192")
    print("  - Staging is REQUIRED for Sonnet to pass both constraints")

    print("\n  HYPOTHESIS: Format-First Interference")
    print("  " + "-"*50)
    print("  Sonnet may have strong format compliance training that")
    print("  interferes with correctness when attempted simultaneously.")
    print("  ")
    print("  Evidence:")
    print("  - Other models (Opus, Haiku) succeed one-shot on both")
    print("  - Sonnet's correctness is fine (100% at delta>=96)")
    print("  - The failure is specifically format compliance")
    print("  - Staging breaks the interference by separating concerns")
    print("  ")
    print("  This is NOT simple rho conflict (which would affect both).")
    print("  It's a DEPENDENCY structure: format-correctness interference.")


def main():
    # Load data
    data_path = Path(__file__).parent / "../../archive/results_intermediary/claude_genome_full.json"
    if not data_path.exists():
        data_path = Path("archive/results_intermediary/claude_genome_full.json")

    genome_data = load_genome_data(str(data_path))

    print("\n" + "="*70)
    print("  CLAUDE GENOME ANALYSIS")
    print("  Mapping Lower Bounds Across the Model Family")
    print("="*70)
    print(f"\n  Experiment: {genome_data['meta']['experiment']}")
    print(f"  API calls: {genome_data['meta']['api_calls']}")
    print(f"  Budget probes: {genome_data['meta']['budget_probes']}")

    # Analyze each model
    analyses = []
    for model in genome_data['results']:
        analysis = analyze_model(model)
        analyses.append(analysis)
        print_analysis(analysis)

    # Sonnet deep dive
    sonnet_deep_dive(genome_data)

    # Summary table
    print("\n" + "="*70)
    print("  SUMMARY: IMPLIED CONFLICT (rho) FROM delta_min RATIOS")
    print("="*70)
    print(f"\n  {'Model':<12} {'delta_a':>6} {'delta_both':>8} {'Ratio':>8} {'rho_implied':>10}")
    print(f"  {'-'*50}")

    for a in analyses:
        name = a['model']
        delta_a = a['delta_min']['oneshot']['pass_a']
        delta_both = a['delta_min']['oneshot']['pass_both']

        if delta_a and delta_both:
            ratio = delta_both / delta_a
            rho = a['rho_implied']
            print(f"  {name:<12} {delta_a:>6} {delta_both:>8} {ratio:>8.2f} {rho:>10.2f}")
        elif delta_a and delta_both is None:
            print(f"  {name:<12} {delta_a:>6} {'NEVER':>8} {'INF':>8} {'1.00':>10}")
        else:
            print(f"  {name:<12} {'N/A':>6} {'N/A':>8} {'N/A':>8} {'N/A':>10}")

    print("\n  INTERPRETATION:")
    print("  " + "-"*50)
    print("  * Haiku/Opus: rho ~ 0 -> constraints are aligned, no diagonal cost")
    print("  * 3.5-Haiku: rho ~ 0.44 -> moderate conflict, small amplification")
    print("  * Sonnet 4.5: rho = 1.0 -> INFINITE diagonal cost one-shot")
    print("  ")
    print("  Sonnet's infinite implied rho suggests something beyond simple")
    print("  constraint conflict: a dependency structure that REQUIRES staging.")


if __name__ == "__main__":
    main()
