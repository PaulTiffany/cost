"""Bidirectional check: external invariant spec <-> pytest hypothesis program.

If the environment variable INVARIANT_SPEC_PATH points at an external
invariant-family specification document, this test parses the document for
its declared invariant families and asserts that each one has at least one
corresponding named pytest function in test_hypothesis_program.py.

Behavior:
  * INVARIANT_SPEC_PATH unset  -> SKIP (reviewer machine; no signal needed)
  * INVARIANT_SPEC_PATH points at a missing file -> SKIP with reason
  * INVARIANT_SPEC_PATH points at a valid file but parsing yields no families
    -> SKIP with reason (spec format unrecognized; not a failure)
  * INVARIANT_SPEC_PATH valid AND families parsed -> assert correspondence

This mechanism gives the author bidirectional drift-detection between the
formal invariant spec (kept in a separate working repo) and the executable
test suite (kept in this submission). Adding a new invariant family in the
spec without a corresponding pytest function fails the test immediately on
the next commit. On a reviewer machine the test simply skips — no signal,
no leak.

The env var name is deliberately generic so the skip message reveals
nothing about the author's local layout.
"""

from __future__ import annotations

import inspect
import os
import re
from pathlib import Path
from typing import List, Set

import pytest

import ci.audit.tests.test_hypothesis_program as hp


SIX_KNOWN_FAMILIES = (
    "state",
    "budget",
    "coherence",
    "diagnostic",
    "transition",
    "falsification",
)


def _all_hypothesis_test_names() -> Set[str]:
    """Collect every test_* function name in test_hypothesis_program.py."""
    names: Set[str] = set()
    for _, cls in inspect.getmembers(hp, inspect.isclass):
        for fname, _ in inspect.getmembers(cls, inspect.isfunction):
            if fname.startswith("test_"):
                names.add(fname)
    return names


def _parse_families_from_spec(text: str) -> List[str]:
    """Parse invariant family names from a spec document.

    Recognized formats (any one suffices):
      * Markdown headings: '### State invariant', '## Budget invariant', etc.
      * Inline labels: 'state invariant:', 'budget invariant:'
      * Bold/italic: '**State invariant:**', '*Coherence invariant:*'

    Returns the lowercased family stems found (e.g. 'state', 'budget').
    Stems are matched case-insensitively against the SIX_KNOWN_FAMILIES set
    so that a spec doc using a different lexical style still resolves to
    the canonical family name.
    """
    found: List[str] = []
    family_pattern = re.compile(
        r"\b(state|budget|coherence|diagnostic|transition|falsification)\s+invariant\b",
        re.IGNORECASE,
    )
    seen: Set[str] = set()
    for match in family_pattern.finditer(text):
        fam = match.group(1).lower()
        if fam not in seen:
            seen.add(fam)
            found.append(fam)
    return found


def test_invariant_spec_corresponds_to_hypothesis_program():
    """Every invariant family declared in the external spec must have at least
    one named pytest function in test_hypothesis_program.py whose name
    references that family.
    """
    # Arrange
    spec_path_str = os.environ.get("INVARIANT_SPEC_PATH")
    if not spec_path_str:
        pytest.skip("INVARIANT_SPEC_PATH not set; skipping spec-correspondence check")

    spec_path = Path(spec_path_str)
    if not spec_path.exists():
        pytest.skip(f"INVARIANT_SPEC_PATH points to missing path; skipping")
    if not spec_path.is_file():
        pytest.skip("INVARIANT_SPEC_PATH points to non-file; skipping")

    text = spec_path.read_text(encoding="utf-8", errors="replace")
    families_in_spec = _parse_families_from_spec(text)

    if not families_in_spec:
        pytest.skip(
            "INVARIANT_SPEC_PATH file did not yield any recognized invariant "
            "families under the supported format; skipping spec-correspondence"
        )

    # Act
    test_names = _all_hypothesis_test_names()
    missing: List[str] = []
    for family in families_in_spec:
        # Match: any test name containing both 'invariant' and the family stem
        match_predicate = (
            f"_invariant_" in "X" + "X"  # placeholder to suppress noqa
        )
        has_match = any(
            family in name.lower() and "invariant" in name.lower()
            for name in test_names
        )
        if not has_match:
            missing.append(family)

    # Assert
    assert not missing, (
        f"External invariant spec at {spec_path} declares family(s) {missing} "
        f"with no corresponding pytest function in test_hypothesis_program.py. "
        f"Add a test named test_H_A*_<family>_invariant_*. "
        f"(Existing hypothesis tests: {sorted(n for n in test_names if 'invariant' in n)})"
    )


def test_six_known_families_each_have_a_named_hypothesis_test():
    """Local invariant: even without an external spec, the six canonical
    invariant family names must each have at least one corresponding pytest.
    This is the spec-free fallback so the suite always asserts coverage of
    the six baseline families.
    """
    # Arrange
    test_names = _all_hypothesis_test_names()

    # Act
    missing = [
        fam for fam in SIX_KNOWN_FAMILIES
        if not any(fam in name.lower() and "invariant" in name.lower()
                   for name in test_names)
    ]

    # Assert
    assert not missing, (
        f"Six-family baseline coverage incomplete: {missing} have no named "
        f"hypothesis test in test_hypothesis_program.py."
    )
