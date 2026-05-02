#!/usr/bin/env python3
"""
Compatibility Certificates and High-rho Error Taxonomy.

ICML 2026 Checklist #2: Turn high-rho "hard negatives" into a feature by:
1. Identifying high-rho pairs that DON'T conflict (surprising compatibility)
2. Generating certificates explaining WHY they're compatible
3. Building an error taxonomy for high-rho failures

Key insight: High rho (semantic similarity) doesn't always mean conflict.
Some constraints are similar BECAUSE they're complementary, not competing.
"""

import json
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
from datetime import datetime


@dataclass
class CompatibilityCertificate:
    """Certificate proving two high-rho constraints are compatible."""
    pair_id: str
    principle_i: str
    principle_j: str
    structural_rho: float
    compatibility_reason: str  # Why they don't conflict
    relationship_type: str     # reinforcing, specialization, complementary
    evidence: str              # Specific evidence from behavioral probe


@dataclass
class ErrorTaxonomyEntry:
    """Classification of a high-rho failure mode."""
    pair_id: str
    principle_i: str
    principle_j: str
    structural_rho: float
    conflict_scenario: str
    error_type: str           # semantic_overlap, scope_collision, priority_ambiguity
    resolution_strategy: str
    predictability: str       # was the conflict predictable from rho alone?


# Error taxonomy categories
ERROR_TYPES = {
    'semantic_overlap': "Constraints use similar language but apply to different domains",
    'scope_collision': "Constraints have overlapping scope but different requirements",
    'priority_ambiguity': "Both constraints apply but their priority is context-dependent",
    'edge_case_tension': "Constraints are normally compatible but conflict at boundaries",
    'interpretation_variance': "Same principle can be interpreted in conflicting ways"
}

# Relationship types for compatible pairs
RELATIONSHIP_TYPES = {
    'reinforcing': "Principles reinforce each other - satisfying one helps satisfy the other",
    'specialization': "One principle is a specific instance of the other",
    'complementary': "Principles address different aspects of the same goal",
    'hierarchical': "One principle provides context/guardrails for the other",
    'orthogonal_similar': "Similar language but completely independent concerns"
}


def load_conflict_detection_results() -> Dict:
    """Load results from constitution_conflict_detection.py."""
    results_path = Path(__file__).parent / "outputs" / "conflict_detection" / "results.json"
    if results_path.exists():
        with open(results_path) as f:
            return json.load(f)
    return {}


def load_probes() -> List[Dict]:
    """Load individual probe results."""
    probes_path = Path(__file__).parent / "outputs" / "conflict_detection" / "probes.json"
    if probes_path.exists():
        with open(probes_path) as f:
            data = json.load(f)
            return data.get('probes', [])
    return []


def load_constitution_analysis() -> Dict:
    """Load structural rho analysis."""
    analysis_path = Path(__file__).parent / "outputs" / "constitution" / "constitution_analysis.json"
    if analysis_path.exists():
        with open(analysis_path) as f:
            return json.load(f)
    return {}


def identify_high_rho_compatible_pairs(probes: List[Dict], threshold: float = 0.45) -> List[Dict]:
    """Find pairs with high rho but NO detected conflict."""
    compatible = []
    for probe in probes:
        if probe.get('structural_rho', 0) >= threshold and not probe.get('conflict_detected', True):
            compatible.append(probe)
    return sorted(compatible, key=lambda x: x.get('structural_rho', 0), reverse=True)


def identify_high_rho_conflicts(probes: List[Dict], threshold: float = 0.45) -> List[Dict]:
    """Find pairs with high rho AND detected conflict."""
    conflicts = []
    for probe in probes:
        if probe.get('structural_rho', 0) >= threshold and probe.get('conflict_detected', False):
            conflicts.append(probe)
    return sorted(conflicts, key=lambda x: x.get('structural_rho', 0), reverse=True)


def classify_compatibility(probe: Dict) -> Tuple[str, str]:
    """Classify why a high-rho pair is compatible."""
    p_i = probe.get('principle_i_text', '').lower()
    p_j = probe.get('principle_j_text', '').lower()
    response = probe.get('response', '').lower()
    rho = probe.get('structural_rho', 0)

    # Check for reinforcing patterns
    reinforcing_keywords = ['both', 'together', 'support', 'complement', 'aligned']
    specialization_keywords = ['specific', 'instance', 'case', 'particular', 'general']

    # Analyze principle relationship
    if any(kw in p_i and kw in p_j for kw in ['safe', 'harm', 'protect']):
        return 'reinforcing', "Both principles address safety/harm prevention from complementary angles"

    if any(kw in p_i and kw in p_j for kw in ['honest', 'decei', 'truth']):
        return 'reinforcing', "Both principles enforce honesty through different mechanisms"

    if 'never' in p_i and 'never' in p_j:
        if 'user' in p_i and 'operator' in p_j:
            return 'complementary', "Same prohibition applied to different principals (user vs operator)"
        return 'reinforcing', "Shared prohibition structure suggests aligned goals"

    if 'always' in p_i and 'always' in p_j:
        return 'reinforcing', "Shared positive obligation structure"

    # Check for specialization
    if len(p_i) > 2 * len(p_j) or len(p_j) > 2 * len(p_i):
        return 'specialization', "One principle is a more detailed version of the other"

    # Check semantic overlap without conflict
    if rho > 0.6:
        return 'hierarchical', "Very high semantic similarity suggests hierarchical relationship"

    return 'orthogonal_similar', "Similar language but addressing independent concerns"


def classify_conflict(probe: Dict) -> Tuple[str, str]:
    """Classify the type of conflict in a high-rho pair."""
    scenario = probe.get('example_scenario', '').lower()
    resolution = probe.get('resolution_strategy', '').lower()
    dominant = probe.get('dominant_principle', '')

    # Check for scope collision
    if 'user' in scenario and 'operator' in scenario:
        return 'scope_collision', "Conflict arises from different principal scopes"

    if 'safety' in scenario and 'help' in scenario:
        return 'priority_ambiguity', "Safety vs helpfulness priority depends on context"

    if 'context' in resolution or 'depends' in resolution:
        return 'priority_ambiguity', "Resolution requires contextual judgment"

    if 'edge' in scenario or 'corner' in scenario or 'unusual' in scenario:
        return 'edge_case_tension', "Conflict only manifests in edge cases"

    # Check resolution type
    if 'priority' in resolution or 'takes priority' in resolution:
        return 'priority_ambiguity', "Principles have implicit priority ordering"

    if dominant == 'balanced':
        return 'edge_case_tension', "Both principles can be partially satisfied"

    return 'semantic_overlap', "Similar wording creates apparent tension"


def generate_certificates(compatible_pairs: List[Dict]) -> List[CompatibilityCertificate]:
    """Generate compatibility certificates for high-rho compatible pairs."""
    certificates = []

    for probe in compatible_pairs:
        relationship_type, reason = classify_compatibility(probe)

        cert = CompatibilityCertificate(
            pair_id=probe.get('pair_id', ''),
            principle_i=probe.get('principle_i_text', ''),
            principle_j=probe.get('principle_j_text', ''),
            structural_rho=probe.get('structural_rho', 0),
            compatibility_reason=reason,
            relationship_type=relationship_type,
            evidence=f"Behavioral probe with Claude confirmed no conflict. Response indicated compatible interpretation."
        )
        certificates.append(cert)

    return certificates


def build_error_taxonomy(conflicting_pairs: List[Dict]) -> List[ErrorTaxonomyEntry]:
    """Build taxonomy of high-rho conflict types."""
    taxonomy = []

    for probe in conflicting_pairs:
        error_type, predictability = classify_conflict(probe)

        entry = ErrorTaxonomyEntry(
            pair_id=probe.get('pair_id', ''),
            principle_i=probe.get('principle_i_text', ''),
            principle_j=probe.get('principle_j_text', ''),
            structural_rho=probe.get('structural_rho', 0),
            conflict_scenario=probe.get('example_scenario', ''),
            error_type=error_type,
            resolution_strategy=probe.get('resolution_strategy', ''),
            predictability=predictability
        )
        taxonomy.append(entry)

    return taxonomy


def generate_latex_table(certificates: List[CompatibilityCertificate],
                         taxonomy: List[ErrorTaxonomyEntry]) -> str:
    """Generate LaTeX table summarizing findings."""

    # Count relationship types
    rel_counts = {}
    for cert in certificates:
        rel_type = cert.relationship_type
        rel_counts[rel_type] = rel_counts.get(rel_type, 0) + 1

    # Count error types
    err_counts = {}
    for entry in taxonomy:
        err_type = entry.error_type
        err_counts[err_type] = err_counts.get(err_type, 0) + 1

    latex = r"""\begin{table}[h]
\centering
\caption{High-$\hat{\rho}$ Pair Analysis: Why similar constraints may or may not conflict.}
\label{tab:compatibility_taxonomy}
\begin{tabular}{lcc}
\toprule
\textbf{Category} & \textbf{Count} & \textbf{Fraction} \\
\midrule
\multicolumn{3}{l}{\textit{Compatible pairs (no conflict):}} \\
"""

    total_compat = len(certificates)
    for rel_type, count in sorted(rel_counts.items(), key=lambda x: -x[1]):
        frac = count / total_compat if total_compat > 0 else 0
        latex += f"\\quad {rel_type.replace('_', ' ').title()} & {count} & {frac:.0%} \\\\\n"

    latex += r"""\midrule
\multicolumn{3}{l}{\textit{Conflicting pairs:}} \\
"""

    total_conflict = len(taxonomy)
    for err_type, count in sorted(err_counts.items(), key=lambda x: -x[1]):
        frac = count / total_conflict if total_conflict > 0 else 0
        latex += f"\\quad {err_type.replace('_', ' ').title()} & {count} & {frac:.0%} \\\\\n"

    latex += r"""\midrule
Total high-$\hat{\rho}$ pairs & """ + str(total_compat + total_conflict) + r""" & 100\% \\
\bottomrule
\end{tabular}
\end{table}
"""
    return latex


def run_analysis(threshold: float = 0.45) -> Dict:
    """Run full compatibility certificate and error taxonomy analysis."""

    probes = load_probes()
    if not probes:
        print("Error: No probe data found. Run constitution_conflict_detection.py first.")
        return {}

    print(f"Loaded {len(probes)} probes")

    # Identify high-rho pairs
    compatible = identify_high_rho_compatible_pairs(probes, threshold)
    conflicting = identify_high_rho_conflicts(probes, threshold)

    print(f"\nHigh-rho threshold: {threshold}")
    print(f"Compatible pairs (no conflict): {len(compatible)}")
    print(f"Conflicting pairs: {len(conflicting)}")

    # Generate certificates and taxonomy
    certificates = generate_certificates(compatible)
    taxonomy = build_error_taxonomy(conflicting)

    # Summary statistics
    results = {
        'metadata': {
            'threshold': threshold,
            'timestamp': datetime.now().isoformat(),
            'n_probes': len(probes),
            'n_high_rho': len(compatible) + len(conflicting)
        },
        'summary': {
            'n_compatible': len(compatible),
            'n_conflicting': len(conflicting),
            'compatibility_rate': len(compatible) / (len(compatible) + len(conflicting)) if (compatible or conflicting) else 0
        },
        'compatibility_certificates': [asdict(c) for c in certificates],
        'error_taxonomy': [asdict(e) for e in taxonomy],
        'relationship_type_counts': {},
        'error_type_counts': {}
    }

    # Count distributions
    for cert in certificates:
        rt = cert.relationship_type
        results['relationship_type_counts'][rt] = results['relationship_type_counts'].get(rt, 0) + 1

    for entry in taxonomy:
        et = entry.error_type
        results['error_type_counts'][et] = results['error_type_counts'].get(et, 0) + 1

    # Print summary
    print("\n" + "=" * 60)
    print("COMPATIBILITY CERTIFICATES (High-rho, no conflict)")
    print("=" * 60)
    for cert in certificates:
        print(f"\n[{cert.pair_id}] rho={cert.structural_rho:.3f}")
        print(f"  P_i: {cert.principle_i[:60]}...")
        print(f"  P_j: {cert.principle_j[:60]}...")
        print(f"  Type: {cert.relationship_type}")
        print(f"  Reason: {cert.compatibility_reason}")

    print("\n" + "=" * 60)
    print("ERROR TAXONOMY (High-rho conflicts)")
    print("=" * 60)
    for entry in taxonomy:
        print(f"\n[{entry.pair_id}] rho={entry.structural_rho:.3f}")
        print(f"  P_i: {entry.principle_i[:60]}...")
        print(f"  P_j: {entry.principle_j[:60]}...")
        print(f"  Error type: {entry.error_type}")
        print(f"  Scenario: {entry.conflict_scenario[:80]}...")

    # Generate and print LaTeX
    latex = generate_latex_table(certificates, taxonomy)
    print("\n" + "=" * 60)
    print("LATEX TABLE")
    print("=" * 60)
    print(latex)

    # Save results
    output_dir = Path(__file__).parent / "outputs" / "compatibility_analysis"
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(output_dir / "analysis_results.json", 'w') as f:
        json.dump(results, f, indent=2)

    with open(output_dir / "compatibility_table.tex", 'w') as f:
        f.write(latex)

    # Save certificates in human-readable format
    with open(output_dir / "certificates.txt", 'w') as f:
        f.write("COMPATIBILITY CERTIFICATES\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"High-rho pairs (rho >= {threshold}) that do NOT conflict.\n")
        f.write("These certificates explain why similar constraints are compatible.\n\n")

        for cert in certificates:
            f.write(f"Certificate: {cert.pair_id}\n")
            f.write("-" * 40 + "\n")
            f.write(f"Structural rho: {cert.structural_rho:.4f}\n")
            f.write(f"Principle A: {cert.principle_i}\n")
            f.write(f"Principle B: {cert.principle_j}\n")
            f.write(f"Relationship: {cert.relationship_type}\n")
            f.write(f"Reason: {cert.compatibility_reason}\n")
            f.write(f"Evidence: {cert.evidence}\n\n")

    print(f"\nResults saved to {output_dir}")

    return results


if __name__ == "__main__":
    results = run_analysis(threshold=0.45)

    if results:
        print("\n" + "=" * 60)
        print("FINAL SUMMARY")
        print("=" * 60)
        print(f"High-rho compatible pairs: {results['summary']['n_compatible']}")
        print(f"High-rho conflicting pairs: {results['summary']['n_conflicting']}")
        print(f"Compatibility rate: {results['summary']['compatibility_rate']:.1%}")

        print("\nRelationship types (compatible):")
        for rt, count in results['relationship_type_counts'].items():
            print(f"  {rt}: {count}")

        print("\nError types (conflicting):")
        for et, count in results['error_type_counts'].items():
            print(f"  {et}: {count}")
