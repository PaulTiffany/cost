"""
ci/audio_manifest_check.py
Standalone checker for ci/audio_manifest.json.

Verifies:
  1. audio_manifest.json parses as valid JSON.
  2. Every listed file exists on disk.
  3. WAV header is readable (stdlib wave.open + getparams).
  4. sha256 matches the manifest (skipped for entries where sha256=='').
  5. File count matches the declared policy:
       ship_two_curated  -> exactly 2 entries with expected_in_submission=true
       ship_all_31       -> exactly 31 entries with expected_in_submission=true
  6. Cross-check: every expected_in_submission=true path is in ci/bundle_manifest.json
     (if bundle_manifest.json exists).

Optional --refresh flag: recomputes sha256, size_bytes, and WAV header params for
all entries that have sha256=='' and writes the updated manifest back.

Output: ci/audio_manifest_results.json (standard result JSON).
Exit codes: 0=pass, 1=soft failures (missing hash data), 2=hard failures.

Usage:
  python ci/audio_manifest_check.py
  python ci/audio_manifest_check.py --refresh
"""

import hashlib
import json
import sys
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "ci" / "audio_manifest.json"
RESULTS_PATH = REPO_ROOT / "ci" / "audio_manifest_results.json"
BUNDLE_MANIFEST_PATH = REPO_ROOT / "ci" / "bundle_manifest.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_wav_params(path: Path) -> dict:
    """Read WAV header parameters using stdlib wave module."""
    with wave.open(str(path), "rb") as wf:
        p = wf.getparams()
        n_frames = p.nframes
        sample_rate = p.framerate
        channels = p.nchannels
        sampwidth = p.sampwidth
        duration_sec = round(n_frames / sample_rate, 6) if sample_rate > 0 else 0
    return {
        "sample_rate": sample_rate,
        "channels": channels,
        "sampwidth": sampwidth,
        "duration_sec": duration_sec,
    }


def check_audio_manifest(refresh: bool = False) -> dict:
    failures_hard = []
    failures_soft = []
    passed = []
    warnings = []

    # --- Check 1: parse manifest ---
    if not MANIFEST_PATH.exists():
        failures_hard.append({
            "check": "manifest_exists",
            "path": str(MANIFEST_PATH),
            "reason": "ci/audio_manifest.json not found",
        })
        return _write_results(failures_hard, failures_soft, passed, warnings)

    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        failures_hard.append({
            "check": "manifest_parse",
            "path": str(MANIFEST_PATH),
            "reason": f"JSON parse error: {e}",
        })
        return _write_results(failures_hard, failures_soft, passed, warnings)

    passed.append({"check": "manifest_parse", "detail": "ci/audio_manifest.json is valid JSON"})

    meta = manifest.get("_meta", {})
    policy = meta.get("policy_decision", "ship_two_curated")
    audio_files = manifest.get("audio_files", [])

    # --- Load bundle_manifest for cross-check ---
    bundle_paths = set()
    bundle_manifest_present = BUNDLE_MANIFEST_PATH.exists()
    if bundle_manifest_present:
        try:
            bm = json.loads(BUNDLE_MANIFEST_PATH.read_text(encoding="utf-8"))
            for entry in bm.get("files", []):
                bundle_paths.add(entry.get("path", "").replace("\\", "/"))
        except Exception as e:
            warnings.append({
                "check": "bundle_manifest_load",
                "reason": f"Could not parse bundle_manifest.json: {e}",
            })

    # --- Per-file checks ---
    refresh_needed = []
    expected_ship_count = 0

    for entry in audio_files:
        rel_path = entry.get("path", "").replace("\\", "/")
        abs_path = REPO_ROOT / rel_path.replace("/", "\\")
        sha256_expected = entry.get("sha256", "")
        size_expected = entry.get("size_bytes", 0)
        expected_in_sub = entry.get("expected_in_submission", False)

        if expected_in_sub:
            expected_ship_count += 1

        # Check 2: file exists
        if not abs_path.exists():
            failures_hard.append({
                "check": "file_exists",
                "path": rel_path,
                "reason": "WAV file not found on disk",
            })
            continue

        # Check 3: WAV header valid
        try:
            wav_params = read_wav_params(abs_path)
        except wave.Error as e:
            failures_hard.append({
                "check": "wav_header",
                "path": rel_path,
                "reason": f"wave.open failed: {e}",
            })
            continue
        except Exception as e:
            failures_hard.append({
                "check": "wav_header",
                "path": rel_path,
                "reason": f"Unexpected error reading WAV header: {e}",
            })
            continue

        actual_size = abs_path.stat().st_size

        # Check 4: sha256 match (only if manifest has a non-empty hash)
        if sha256_expected:
            actual_sha256 = sha256_file(abs_path)
            if actual_sha256 != sha256_expected:
                failures_hard.append({
                    "check": "sha256",
                    "path": rel_path,
                    "expected": sha256_expected,
                    "actual": actual_sha256,
                    "reason": "SHA-256 mismatch",
                })
            else:
                passed.append({"check": "sha256", "path": rel_path, "detail": "hash matches"})

            # Also verify size if manifest says non-zero
            if size_expected and actual_size != size_expected:
                failures_hard.append({
                    "check": "size_bytes",
                    "path": rel_path,
                    "expected": size_expected,
                    "actual": actual_size,
                    "reason": "size_bytes mismatch",
                })
        else:
            failures_soft.append({
                "check": "sha256_missing",
                "path": rel_path,
                "reason": "sha256 not yet computed in manifest; run --refresh",
            })
            refresh_needed.append(entry)

        # Collect WAV params for refresh
        if refresh:
            actual_sha256 = sha256_file(abs_path)
            entry["sha256"] = actual_sha256
            entry["size_bytes"] = actual_size
            entry["duration_sec"] = wav_params["duration_sec"]
            entry["sample_rate"] = wav_params["sample_rate"]
            entry["channels"] = wav_params["channels"]
            entry["sampwidth"] = wav_params["sampwidth"]
            passed.append({
                "check": "refresh",
                "path": rel_path,
                "detail": f"sha256={actual_sha256[:16]}... duration={wav_params['duration_sec']}s sr={wav_params['sample_rate']}",
            })

        # Check 6: expected_in_submission files are in bundle_manifest
        if expected_in_sub and bundle_manifest_present:
            norm = rel_path.lstrip("/")
            if norm not in bundle_paths:
                failures_hard.append({
                    "check": "bundle_manifest_cross_check",
                    "path": rel_path,
                    "reason": "expected_in_submission=true but path not in ci/bundle_manifest.json",
                })
            else:
                passed.append({
                    "check": "bundle_manifest_cross_check",
                    "path": rel_path,
                    "detail": "present in bundle_manifest",
                })

    # Check 5: count matches policy
    if policy == "ship_two_curated":
        if expected_ship_count != 2:
            failures_hard.append({
                "check": "policy_count",
                "policy": policy,
                "expected_ship_count": 2,
                "actual_ship_count": expected_ship_count,
                "reason": f"policy=ship_two_curated requires exactly 2 expected_in_submission=true, found {expected_ship_count}",
            })
        else:
            passed.append({
                "check": "policy_count",
                "detail": f"policy={policy}: {expected_ship_count} files marked for submission",
            })
    elif policy == "ship_all_31":
        if expected_ship_count != 31:
            failures_hard.append({
                "check": "policy_count",
                "policy": policy,
                "expected_ship_count": 31,
                "actual_ship_count": expected_ship_count,
                "reason": f"policy=ship_all_31 requires all 31 expected_in_submission=true, found {expected_ship_count}",
            })
        else:
            passed.append({
                "check": "policy_count",
                "detail": f"policy={policy}: {expected_ship_count} files marked for submission",
            })
    else:
        warnings.append({
            "check": "policy_count",
            "reason": f"Unknown policy_decision '{policy}'; skipping count check",
        })

    # Total file count check
    total = len(audio_files)
    if total != 31:
        failures_hard.append({
            "check": "total_file_count",
            "expected": 31,
            "actual": total,
            "reason": f"Manifest should enumerate exactly 31 audio files, found {total}",
        })
    else:
        passed.append({"check": "total_file_count", "detail": f"{total} entries in manifest"})

    # Write refreshed manifest if requested
    if refresh and not failures_hard:
        MANIFEST_PATH.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        passed.append({
            "check": "refresh_write",
            "detail": f"Wrote refreshed manifest to {MANIFEST_PATH}",
        })

    return _write_results(failures_hard, failures_soft, passed, warnings)


def _write_results(failures_hard, failures_soft, passed, warnings) -> dict:
    n_hard = len(failures_hard)
    n_soft = len(failures_soft)
    n_pass = len(passed)
    n_warn = len(warnings)

    if n_hard > 0:
        status = "FAIL"
        exit_code = 2
    elif n_soft > 0:
        status = "SOFT_FAIL"
        exit_code = 1
    else:
        status = "PASS"
        exit_code = 0

    result = {
        "check": "audio_manifest_check",
        "status": status,
        "exit_code": exit_code,
        "summary": {
            "hard_failures": n_hard,
            "soft_failures": n_soft,
            "passed": n_pass,
            "warnings": n_warn,
        },
        "hard_failures": failures_hard,
        "soft_failures": failures_soft,
        "passed": passed,
        "warnings": warnings,
    }

    RESULTS_PATH.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return result


def main():
    refresh = "--refresh" in sys.argv
    result = check_audio_manifest(refresh=refresh)

    status = result["status"]
    s = result["summary"]
    print(
        f"audio_manifest_check: {status} "
        f"(hard={s['hard_failures']} soft={s['soft_failures']} "
        f"pass={s['passed']} warn={s['warnings']})"
    )

    if result["hard_failures"]:
        print("\nHard failures:")
        for f in result["hard_failures"]:
            print(f"  [{f['check']}] {f.get('path', '')} - {f['reason']}")

    if result["soft_failures"]:
        print("\nSoft failures (--refresh to fix):")
        for f in result["soft_failures"][:5]:
            print(f"  [{f['check']}] {f.get('path', '')} - {f['reason']}")
        if len(result["soft_failures"]) > 5:
            print(f"  ... and {len(result['soft_failures']) - 5} more")

    if result["warnings"]:
        print("\nWarnings:")
        for w in result["warnings"]:
            print(f"  [{w['check']}] {w.get('reason', '')}")

    print(f"\nResults written to: {RESULTS_PATH}")
    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()
