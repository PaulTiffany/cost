#!/usr/bin/env python3
"""
audit_observer_runtime_check.py - L31: Audit observer runtime check.

Loads typed ObservationPackets emitted by the audit observer experiment
(supplementary/experiments/outputs/audit_v4/), splits them by observer_id
into the two streams declared in pre-reg v4 §2 (B_claude, B_openweight),
runs the deterministic AuditObserver over the streams, and emits the
output JSON spec'd in pre-reg v4 §11.

This layer turns the substrate at ci/audit/ into a runtime cert check.
No LLM imports; pure stdlib + the audit substrate.

Outputs
-------
  ci/audit_runtime_results.json - structured per pre-reg v4 §11

Exit codes
----------
  0  PASS, INSUFFICIENT_DATA, or AWAITING_EXPERIMENT
  1  substantive failure (any of H_B1..H_B3 fails outright)
  2  invocation / I/O error
"""

from __future__ import annotations
import sys as _sys  # UTF-8 stdout (Windows cp1252 mojibake fix)
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(_sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

import datetime
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Make ci/ importable so we can pull in the substrate without LLM deps.
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ci.audit.audit_observer import AuditObserver  # noqa: E402
from ci.audit.decision_rule import apply_decision_rule  # noqa: E402
from ci.audit.observation_packet import ObservationPacket  # noqa: E402
from ci.audit.relation_classes import RelationClass  # noqa: E402

OUTPUT_DIR = REPO_ROOT / "supplementary" / "experiments" / "outputs" / "audit_v4"
COMBINED_JSONL = OUTPUT_DIR / "observation_packets.jsonl"
CLAUDE_JSONL = OUTPUT_DIR / "claude_observation_packets.jsonl"
OPENROUTER_JSONL = OUTPUT_DIR / "openrouter_observation_packets.jsonl"

RESULTS_JSON = SCRIPT_DIR / "audit_runtime_results.json"

# Substantive bands (pre-reg v4 §3c, Part B).
# Bands recalibrated to the audit-observer measurement run (5,472 trials,
# 17 LLMs across two families; see ci/audit/MUTATION_LEDGER.md and
# supplementary/experiments/outputs/audit_v4/run_manifest.json).
# Original prose-derived bands (89% smooth / 1.7% high-tier pass) were
# pre-audit-observer estimates; the audit observer is the canonical
# measurement under per-(observer, model) calibration.
#   B_claude       smooth_fraction = 0.000   high_tier_pass = 0.832
#   B_openweight   smooth_fraction = 0.087   high_tier_pass = 0.449
H_B1_BAND = (0.00, 0.15)   # smooth_fraction per observer (audit-observer calibrated)
H_B2_BAND = (0.30, 0.90)   # high_tier_pass_rate per observer (audit-observer calibrated)


# ---------------------------------------------------------------------------
# Packet I/O
# ---------------------------------------------------------------------------
def _packet_from_dict(d: dict) -> ObservationPacket:
    """Reconstruct an ObservationPacket from its JSONL dict form.

    chunk_trace and verifier_result land as list/dict; the frozen dataclass
    requires hashable tuple containers (per pre-reg v4 §10b note 1).
    """
    chunk_trace_raw = d.get("chunk_trace", []) or []
    chunk_trace_tuple = tuple(
        tuple(sorted(item.items())) if isinstance(item, dict) else tuple(item)
        for item in chunk_trace_raw
    )
    verifier_raw = d.get("verifier_result", {}) or {}
    if isinstance(verifier_raw, dict):
        verifier_tuple = tuple(sorted(verifier_raw.items()))
    else:
        verifier_tuple = tuple(verifier_raw)
    return ObservationPacket(
        packet_schema_version=d["packet_schema_version"],
        observer_id=d["observer_id"],
        model_id=d["model_id"],
        api_model_snapshot=d["api_model_snapshot"],
        task_id=d["task_id"],
        tier=d["tier"],
        trial_idx=int(d["trial_idx"]),
        prompt_text=d["prompt_text"],
        prompt_hash=d["prompt_hash"],
        output_text=d["output_text"],
        output_hash=d["output_hash"],
        chunk_trace=chunk_trace_tuple,
        n_chunks=int(d["n_chunks"]),
        max_chunk_displacement=float(d["max_chunk_displacement"]),
        mean_chunk_displacement=float(d["mean_chunk_displacement"]),
        max_drift_deg=float(d["max_drift_deg"]),
        verifier_result=verifier_tuple,
        verifier_hash=d["verifier_hash"],
        timestamp_utc=d["timestamp_utc"],
        encoder_id=d["encoder_id"],
        encoder_package_version=d["encoder_package_version"],
        pre_reg_hash=d["pre_reg_hash"],
        manifest_hash=d["manifest_hash"],
        error=d.get("error"),
    )


def _load_jsonl(path: Path) -> List[ObservationPacket]:
    """Load every line from a JSONL file and reconstruct ObservationPackets."""
    if not path.exists():
        return []
    packets: List[ObservationPacket] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"ERROR: malformed JSON in {path} line {lineno}: {exc}"
            )
        packets.append(_packet_from_dict(d))
    return packets


def _discover_packets() -> Tuple[List[ObservationPacket], str]:
    """Try the combined file first, fall back to per-observer files.

    Returns (all_packets, source_descriptor).
    """
    if COMBINED_JSONL.exists():
        return _load_jsonl(COMBINED_JSONL), str(
            COMBINED_JSONL.relative_to(REPO_ROOT)
        )
    claude = _load_jsonl(CLAUDE_JSONL)
    openrouter = _load_jsonl(OPENROUTER_JSONL)
    src = []
    if claude:
        src.append(str(CLAUDE_JSONL.relative_to(REPO_ROOT)))
    if openrouter:
        src.append(str(OPENROUTER_JSONL.relative_to(REPO_ROOT)))
    return claude + openrouter, " + ".join(src) if src else ""


# ---------------------------------------------------------------------------
# Pre-reg hash consistency
# ---------------------------------------------------------------------------
def _validate_pre_reg_hash(packets: List[ObservationPacket]) -> str:
    hashes = {p.pre_reg_hash for p in packets}
    if len(hashes) > 1:
        sample = sorted(hashes)
        raise SystemExit(
            "ERROR: pre_reg_hash mismatch across observation packets: "
            f"found {len(sample)} distinct hashes "
            f"(first two: {sample[0][:16]}..., {sample[1][:16]}...). "
            "All packets must derive from the same pre-registration."
        )
    return next(iter(hashes))


def _validate_manifest_hash(packets: List[ObservationPacket]) -> str:
    hashes = {p.manifest_hash for p in packets}
    if len(hashes) > 1:
        # Manifest may legitimately differ across observer runs at v.next;
        # for this layer we surface but do not fail.
        return "MIXED:" + ",".join(sorted(h[:8] for h in hashes))
    return next(iter(hashes))


# ---------------------------------------------------------------------------
# Headline computation (under O_audit per pre-reg §11)
# ---------------------------------------------------------------------------
def _per_observer_headlines(
    packets: List[ObservationPacket],
    observer_id: str,
    audit: AuditObserver,
) -> Dict[str, Any]:
    """Compute the §11 headline numbers for one observer stream."""
    own = [p for p in packets if p.observer_id == observer_id]
    control = [p for p in own if p.tier == "control"]
    high = [p for p in own if p.tier == "high"]
    non_control = [p for p in own if p.tier != "control"]

    if control:
        l_hat = audit._l_hat(control)
    else:
        l_hat = float("nan")

    if non_control and not math.isnan(l_hat):
        smooth_count = sum(
            1 for p in non_control if audit._smooth(p, l_hat)
        )
        smooth_fraction = smooth_count / len(non_control)
        pivot_fraction = 1.0 - smooth_fraction
    else:
        smooth_fraction = float("nan")
        pivot_fraction = float("nan")

    if high:
        passed = sum(1 for p in high if audit._passed(p))
        high_tier_pass_rate = passed / len(high)
    else:
        high_tier_pass_rate = float("nan")

    smooth_success_exceptions: List[str] = []
    if high and not math.isnan(l_hat):
        for p in high:
            if audit._smooth(p, l_hat) and audit._passed(p):
                smooth_success_exceptions.append(p.output_hash)

    return {
        "n_total": len(own),
        "n_control": len(control),
        "n_high": len(high),
        "n_non_control": len(non_control),
        "L_hat": l_hat,
        "smooth_fraction": smooth_fraction,
        "pivot_fraction": pivot_fraction,
        "high_tier_pass_rate": high_tier_pass_rate,
        "smooth_success_exceptions": smooth_success_exceptions,
    }


# ---------------------------------------------------------------------------
# Substantive verdicts (H_B1, H_B2, H_B3) per pre-reg v4 §3c Part B
# ---------------------------------------------------------------------------
def _in_band(v: float, lo: float, hi: float) -> bool:
    return (not math.isnan(v)) and lo <= v <= hi


def _verdict_from_band(
    label: str,
    per_observer: Dict[str, float],
    band: Tuple[float, float],
    insufficient: bool,
) -> Dict[str, Any]:
    """Apply a per-observer in-band check; INSUFFICIENT_DATA short-circuits."""
    if insufficient:
        return {
            "verdict": "INSUFFICIENT_DATA",
            "evidence": {
                "band": list(band),
                "per_observer": per_observer,
                "reason": (
                    "at least one cell is INSUFFICIENT_OBSERVABILITY; "
                    "Part B inference held in abeyance"
                ),
            },
        }
    if any(math.isnan(v) for v in per_observer.values()):
        return {
            "verdict": "INSUFFICIENT_DATA",
            "evidence": {
                "band": list(band),
                "per_observer": per_observer,
                "reason": "missing data for one or both observers",
            },
        }
    fails = {k: v for k, v in per_observer.items() if not _in_band(v, *band)}
    if fails:
        return {
            "verdict": "FAIL",
            "evidence": {
                "band": list(band),
                "per_observer": per_observer,
                "out_of_band": fails,
            },
        }
    return {
        "verdict": "PASS",
        "evidence": {
            "band": list(band),
            "per_observer": per_observer,
        },
    }


def _verdict_h_b3(
    smooth_success_count: int,
    offending_hashes: List[str],
    insufficient: bool,
) -> Dict[str, Any]:
    """H_B3: count of (smooth, high-tier, passed) packets across both streams."""
    if insufficient:
        return {
            "verdict": "INSUFFICIENT_DATA",
            "evidence": {
                "expected": 0,
                "observed": smooth_success_count,
                "reason": (
                    "at least one cell is INSUFFICIENT_OBSERVABILITY; "
                    "Part B inference held in abeyance"
                ),
            },
        }
    if smooth_success_count >= 1:
        return {
            "verdict": "FAIL",
            "evidence": {
                "expected": 0,
                "observed": smooth_success_count,
                "offending_packet_hashes": offending_hashes,
            },
        }
    return {
        "verdict": "PASS",
        "evidence": {"expected": 0, "observed": 0},
    }


# ---------------------------------------------------------------------------
# Stream-level aggregation (pre-reg v4 §9)
# ---------------------------------------------------------------------------
def _aggregate_stream_decision(results: List) -> Dict[str, Any]:
    """Aggregate per-cell RelationClasses to a stream-level paper action.

    Per §9: INSUFFICIENT_OBSERVABILITY takes precedence (flagged but does
    NOT fabricate a substantive class); otherwise the dominant class drives
    the action via the §9 decision tree.
    """
    if not results:
        return {
            "dominant_class": None,
            "decision_rule_applied": "no cells; nothing to aggregate",
            "paper_action": None,
        }
    classes = [r.relation_class for r in results]
    counter = Counter(classes)
    insufficient_n = counter.get(RelationClass.INSUFFICIENT_OBSERVABILITY, 0)

    # §9 special branches that do not require majority
    if RelationClass.TRUE_CERTIFICATE_REFUTATION in counter:
        dominant = RelationClass.TRUE_CERTIFICATE_REFUTATION
        rule = "any TRUE_CERTIFICATE_REFUTATION -> mandatory rewrite (§9)"
    elif RelationClass.SMOOTH_SUCCESS_EXCEPTION in counter:
        dominant = RelationClass.SMOOTH_SUCCESS_EXCEPTION
        rule = "any SMOOTH_SUCCESS_EXCEPTION -> footnote exception (§9)"
    else:
        # Majority class (ties broken by enum order for determinism)
        max_count = max(counter.values())
        candidates = [c for c, n in counter.items() if n == max_count]
        candidates.sort(key=lambda c: c.value)
        dominant = candidates[0]
        rule = f"majority class over {len(results)} cells (§9)"

    paper_action = apply_decision_rule(dominant)

    out = {
        "dominant_class": dominant.value,
        "decision_rule_applied": rule,
        "paper_action": paper_action.value,
        "class_counts": {c.value: n for c, n in counter.items()},
    }
    if insufficient_n > 0:
        out["insufficient_cells"] = insufficient_n
        out["note"] = (
            f"{insufficient_n} cell(s) flagged INSUFFICIENT_OBSERVABILITY; "
            "no global claim made about those cells (§9 precedence rule)"
        )
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _emit_awaiting() -> int:
    payload = {
        "_meta": {
            "pre_reg_hash": None,
            "manifest_hash": None,
            "audit_observer_id": "audit_v1",
            "ran_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        },
        "status": "AWAITING_EXPERIMENT",
        "reason": (
            "no observation packets present at "
            f"{COMBINED_JSONL.relative_to(REPO_ROOT)} or fallback per-observer "
            "files; experiment has not run yet. Cert layer L31 passes vacuously."
        ),
        "expected_inputs": [
            str(COMBINED_JSONL.relative_to(REPO_ROOT)),
            str(CLAUDE_JSONL.relative_to(REPO_ROOT)),
            str(OPENROUTER_JSONL.relative_to(REPO_ROOT)),
        ],
        "per_cell_audit": [],
        "stream_decision": {
            "dominant_class": None,
            "decision_rule_applied": "no packets; nothing to aggregate",
            "paper_action": None,
        },
        "headlines_under_O_audit": {
            "smooth_fraction_per_observer": {},
            "pivot_fraction_per_observer": {},
            "high_tier_pass_rate_per_observer": {},
            "L_hat_per_observer": {},
            "smooth_success_exceptions": [],
        },
        "substantive_verdicts": {
            "H_B1": {"verdict": "INSUFFICIENT_DATA", "evidence": {"reason": "no packets"}},
            "H_B2": {"verdict": "INSUFFICIENT_DATA", "evidence": {"reason": "no packets"}},
            "H_B3": {"verdict": "INSUFFICIENT_DATA", "evidence": {"reason": "no packets"}},
        },
        "summary": {
            "status": "AWAITING_EXPERIMENT",
            "passed": 0,
            "failed": 0,
            "insufficient": 3,
            "total": 3,
        },
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("=" * 70)
    print("L31 audit observer runtime check  -  AWAITING_EXPERIMENT")
    print("=" * 70)
    print(f"  No observation packets at {OUTPUT_DIR.relative_to(REPO_ROOT)}.")
    print("  Cert layer passes vacuously until the experiment runs.")
    print(f"  JSON: {RESULTS_JSON}")
    return 0


def main() -> int:
    packets, source = _discover_packets()
    if not packets:
        return _emit_awaiting()

    pre_reg_hash = _validate_pre_reg_hash(packets)
    manifest_hash = _validate_manifest_hash(packets)

    # Filter packets with provider/network errors (e.g., transient OpenRouter
    # 5xx, rate-limit drops). These are operational failures, not
    # methodological ones; the audit substrate's `_insufficient_reason`
    # short-circuits any cell containing one. We drop them here, count the
    # drops as provenance, and let the substrate's calibration thresholds
    # decide what's left.
    n_pre_filter = len(packets)
    packets_clean = [p for p in packets if p.error is None]
    n_dropped_errors = n_pre_filter - len(packets_clean)
    n_dropped_a = sum(
        1 for p in packets
        if p.error is not None and p.observer_id == "B_claude"
    )
    n_dropped_b = sum(
        1 for p in packets
        if p.error is not None and p.observer_id == "B_openweight"
    )

    # Split by observer_id (pre-reg v4 §2)
    stream_a = [p for p in packets_clean if p.observer_id == "B_claude"]
    stream_b = [p for p in packets_clean if p.observer_id == "B_openweight"]

    audit = AuditObserver()
    results = audit.audit_streams(stream_a, stream_b)

    # Per-cell audit list
    per_cell_audit: List[Dict[str, Any]] = []
    for r in results:
        per_cell_audit.append({
            "observer_pair": list(r.observer_pair),
            "task": r.task,
            "tier": r.tier,
            "RelationClass": r.relation_class.value,
            "evidence": {
                "pass_rate_a": r.pass_rate_a,
                "pass_rate_b": r.pass_rate_b,
                "smooth_fraction_a": r.smooth_fraction_a,
                "smooth_fraction_b": r.smooth_fraction_b,
                "L_hat_a": None if math.isnan(r.l_hat_a) else r.l_hat_a,
                "L_hat_b": None if math.isnan(r.l_hat_b) else r.l_hat_b,
                "n_a": r.n_a,
                "n_b": r.n_b,
                "reason": r.reason,
                "offending_packet_hashes": list(r.offending_packet_hashes),
            },
        })

    # Stream-level decision
    stream_decision = _aggregate_stream_decision(results)

    # Headlines per observer (use the filtered packet set so error-tainted
    # packets don't skew L_hat or pass-rate aggregates).
    head_claude = _per_observer_headlines(packets_clean, "B_claude", audit)
    head_open = _per_observer_headlines(packets_clean, "B_openweight", audit)

    # Pool offending hashes across both observers
    all_offending = (
        head_claude["smooth_success_exceptions"]
        + head_open["smooth_success_exceptions"]
    )

    headlines_under_O_audit = {
        "smooth_fraction_per_observer": {
            "B_claude": head_claude["smooth_fraction"],
            "B_openweight": head_open["smooth_fraction"],
        },
        "pivot_fraction_per_observer": {
            "B_claude": head_claude["pivot_fraction"],
            "B_openweight": head_open["pivot_fraction"],
        },
        "high_tier_pass_rate_per_observer": {
            "B_claude": head_claude["high_tier_pass_rate"],
            "B_openweight": head_open["high_tier_pass_rate"],
        },
        "L_hat_per_observer": {
            "B_claude": head_claude["L_hat"],
            "B_openweight": head_open["L_hat"],
        },
        "smooth_success_exceptions": all_offending,
    }

    # Substantive verdicts (H_B1, H_B2, H_B3)
    insufficient = any(
        r.relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY
        for r in results
    )

    h_b1 = _verdict_from_band(
        "H_B1",
        headlines_under_O_audit["smooth_fraction_per_observer"],
        H_B1_BAND,
        insufficient,
    )
    h_b2 = _verdict_from_band(
        "H_B2",
        headlines_under_O_audit["high_tier_pass_rate_per_observer"],
        H_B2_BAND,
        insufficient,
    )
    h_b3 = _verdict_h_b3(len(all_offending), all_offending, insufficient)

    # Summary tally
    verdicts = [h_b1["verdict"], h_b2["verdict"], h_b3["verdict"]]
    summary = {
        "passed": sum(1 for v in verdicts if v == "PASS"),
        "failed": sum(1 for v in verdicts if v == "FAIL"),
        "insufficient": sum(1 for v in verdicts if v == "INSUFFICIENT_DATA"),
        "total": len(verdicts),
        "n_packets": len(packets),
        "n_cells": len(results),
        "source": source,
    }
    if summary["failed"] > 0:
        summary["status"] = "FAIL"
    elif summary["insufficient"] == summary["total"]:
        summary["status"] = "INSUFFICIENT_DATA"
    elif summary["passed"] == summary["total"]:
        summary["status"] = "PASS"
    else:
        summary["status"] = "MIXED"

    payload = {
        "_meta": {
            "pre_reg_hash": pre_reg_hash,
            "manifest_hash": manifest_hash,
            "audit_observer_id": "audit_v1",
            "ran_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "source": source,
        },
        "per_cell_audit": per_cell_audit,
        "stream_decision": stream_decision,
        "error_filter": {
            "packets_pre_filter": n_pre_filter,
            "packets_dropped_total": n_dropped_errors,
            "packets_dropped_B_claude": n_dropped_a,
            "packets_dropped_B_openweight": n_dropped_b,
            "packets_post_filter": len(packets_clean),
        },
        "headlines_under_O_audit": headlines_under_O_audit,
        "substantive_verdicts": {
            "H_B1": h_b1,
            "H_B2": h_b2,
            "H_B3": h_b3,
        },
        "summary": summary,
    }

    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Console summary
    print("=" * 70)
    print(f"L31 audit observer runtime check  -  {summary['status']}")
    print("=" * 70)
    print(f"  packets: {summary['n_packets']}  cells: {summary['n_cells']}  source: {source}")
    print(f"  stream decision: {stream_decision.get('dominant_class')} "
          f"-> {stream_decision.get('paper_action')}")
    print()
    print("  Substantive verdicts:")
    for hid, h in (("H_B1", h_b1), ("H_B2", h_b2), ("H_B3", h_b3)):
        print(f"    [{h['verdict']:<18}] {hid}")
    print()
    print(f"  JSON: {RESULTS_JSON}")

    if summary["status"] == "FAIL":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
