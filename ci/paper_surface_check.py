"""Paper surface impacting cert.

Mechanically certifies that no two surface objects on a page collide
(impact) where they shouldn't. Five categories:

    card_overflow     text inside a filled rect extends past its edges
    block_collision   two text blocks (paragraphs) overlap
    text_image        a text block overlays a raster image
    image_image       two raster images overlap
    drawing_drawing   two filled drawing rects overlap (excluding nesting)

False-positive controls:
    - hyperlink-annotation overlay is exempt (PDF links overlap text by design)
    - one rect fully containing another is exempt (cards-in-figure, panels)
    - PyMuPDF blocks share their parent page coordinate space; subscripts
      inside a line block are not separate blocks, so within-paragraph math
      kerning is not flagged
    - tolerance thresholds tuned per category to avoid hairline noise

Limitations (be honest about what this cert cannot see):
    - Vector content inside an `\\includegraphics{*.pdf}` is opaque to the
      outer doc. The cert sees one atomic image rect; internal collisions
      inside that PDF are invisible.
    - Color-on-color illegibility (white text on white fill) is not checked.
    - "Visually crowded but not overlapping" is stylistic, not certified.

Usage:
    python ci/paper_surface_check.py
    python ci/paper_surface_check.py --scope body
    python ci/paper_surface_check.py --checks card_overflow,block_collision
    python ci/paper_surface_check.py --pdf paper/main.pdf --json-out ci/paper_surface_results.json

Exit:
    0 if no impactions detected
    1 if any impaction detected
    2 on invocation error (missing PDF, fitz import error)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PDF = REPO / "paper" / "main.pdf"
DEFAULT_OUT = REPO / "ci" / "paper_surface_results.json"

BODY_PAGE_LIMIT = 9

# Per-category tolerances (pt or pt^2 as noted).
EDGE_TOLERANCE = 1.0           # pt; card overflow edge slack
BLOCK_OVERLAP_AREA = 4.0       # pt^2; text-block vs text-block overlap area
TEXT_IMAGE_OVERLAP_AREA = 4.0  # pt^2; text-block vs image overlap
IMAGE_IMAGE_OVERLAP_AREA = 16.0  # pt^2; image vs image (panels may touch)
DRAWING_OVERLAP_AREA = 16.0    # pt^2; drawing vs drawing
CONTAINMENT_TOLERANCE = 2.0    # pt; rect-fully-inside-rect slack

# Card-overflow category constants (preserved from earlier cert).
MIN_CARD_AREA = 200.0
MAX_CARD_AREA = 80000.0
MIN_CARD_DIM = 30.0
MIN_SPAN_LEN = 2

CHECK_CHOICES = ["card_overflow", "block_collision", "text_image",
                 "image_image", "drawing_drawing", "margin_overflow"]
# Default checks: block_collision is excluded by default because PyMuPDF's
# block segmentation on math-heavy academic PDFs (theorem environments,
# display equations, inline math) produces too many spurious "overlaps"
# between adjacent paragraph fragments to be useful as a hard cert. It is
# kept reachable via `--checks block_collision` for manual auditing.
DEFAULT_CHECKS = ["card_overflow", "text_image", "image_image",
                  "drawing_drawing", "margin_overflow"]

# NeurIPS letter-paper body region. Standard 1in side margins. We check
# only horizontal overflow because page numbers and footers can legitimately
# extend below the body box, while horizontal overflow (wide tables, wide
# figures) is the common "ink past the column" failure NeurIPS reviewers
# reject for. Tolerance is 1pt; NeurIPS template hairlines are well below.
NEURIPS_BODY_LEFT_PT = 72.0
NEURIPS_BODY_RIGHT_INSET_PT = 72.0  # subtracted from page width
MARGIN_TOLERANCE_PT = 1.0


Rect = tuple[float, float, float, float]


@dataclass
class Violation:
    category: str
    page: int
    rect_a: Rect
    rect_b: Rect
    detail: str
    severity_pt: float  # area in pt^2 for collisions, edge pt for overflows


def _rect_area(r: Rect) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def _intersect_area(a: Rect, b: Rect) -> float:
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return w * h


def _bbox_intersects(a: Rect, b: Rect) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _contains(outer: Rect, inner: Rect, slack: float = CONTAINMENT_TOLERANCE) -> bool:
    return (outer[0] - slack <= inner[0] and inner[2] <= outer[2] + slack
            and outer[1] - slack <= inner[1] and inner[3] <= outer[3] + slack)


def _centroid_inside(span: Rect, card: Rect) -> bool:
    cx = (span[0] + span[2]) / 2.0
    cy = (span[1] + span[3]) / 2.0
    return card[0] <= cx <= card[2] and card[1] <= cy <= card[3]


def _spans_relevant_to_card(span: Rect, card: Rect) -> bool:
    if _centroid_inside(span, card):
        return True
    if not _bbox_intersects(span, card):
        return False
    cx = (span[0] + span[2]) / 2.0
    cy = (span[1] + span[3]) / 2.0
    span_h = span[3] - span[1]
    span_w = span[2] - span[0]
    horiz = (cx < card[0] and (card[0] - cx) < span_w) or \
            (cx > card[2] and (cx - card[2]) < span_w)
    vert = (cy < card[1] and (card[1] - cy) < span_h) or \
           (cy > card[3] and (cy - card[3]) < span_h)
    return horiz or vert


def _edge_violation(span: Rect, card: Rect) -> float:
    left = max(0.0, card[0] - span[0])
    right = max(0.0, span[2] - card[2])
    top = max(0.0, card[1] - span[1])
    bot = max(0.0, span[3] - card[3])
    return max(left, right, top, bot)


def _collect_card_rects(page) -> list[Rect]:
    cards: list[Rect] = []
    for d in page.get_drawings():
        rect = d.get("rect")
        if rect is None:
            continue
        r = (rect.x0, rect.y0, rect.x1, rect.y1)
        a = _rect_area(r)
        if a < MIN_CARD_AREA or a > MAX_CARD_AREA:
            continue
        w = r[2] - r[0]
        h = r[3] - r[1]
        if w < MIN_CARD_DIM or h < MIN_CARD_DIM:
            continue
        if d.get("type") not in ("f", "fs"):
            continue
        fill = d.get("fill")
        if fill is None:
            continue
        if isinstance(fill, (tuple, list)) and len(fill) >= 3:
            if all(c > 0.95 for c in fill[:3]):
                continue
        cards.append(r)
    return cards


def _collect_text_spans(page) -> list[tuple[Rect, str]]:
    spans: list[tuple[Rect, str]] = []
    info = page.get_text("dict")
    for block in info.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = span.get("text", "").strip()
                if len(txt) < MIN_SPAN_LEN:
                    continue
                bbox = span.get("bbox")
                if bbox is None:
                    continue
                spans.append((tuple(bbox), txt))
    return spans


def _collect_text_blocks(page) -> list[tuple[Rect, str]]:
    """Block-level groupings (paragraph-ish). type 0 = text, type 1 = image."""
    blocks: list[tuple[Rect, str]] = []
    for b in page.get_text("blocks"):
        if len(b) < 6:
            continue
        x0, y0, x1, y1, text, _block_no = b[0], b[1], b[2], b[3], b[4], b[5]
        block_type = b[6] if len(b) >= 7 else 0
        if block_type != 0:
            continue
        if not text or not text.strip():
            continue
        if (x1 - x0) < 5 or (y1 - y0) < 5:
            continue
        blocks.append(((x0, y0, x1, y1), text.strip().replace("\n", " ")[:80]))
    return blocks


def _collect_image_rects(page) -> list[Rect]:
    rects: list[Rect] = []
    for img in page.get_images(full=True):
        xref = img[0]
        for r in page.get_image_rects(xref):
            rects.append((r.x0, r.y0, r.x1, r.y1))
    return rects


def _collect_link_rects(page) -> list[Rect]:
    rects: list[Rect] = []
    for link in page.get_links():
        r = link.get("from")
        if r is None:
            continue
        rects.append((r.x0, r.y0, r.x1, r.y1))
    return rects


def _under_link(rect: Rect, links: list[Rect]) -> bool:
    for lr in links:
        if _intersect_area(rect, lr) > 0.5 * _rect_area(rect):
            return True
    return False


def check_card_overflow(page, page_idx: int) -> list[Violation]:
    out: list[Violation] = []
    cards = _collect_card_rects(page)
    spans = _collect_text_spans(page)
    for card in cards:
        for span_rect, span_text in spans:
            if not _spans_relevant_to_card(span_rect, card):
                continue
            viol = _edge_violation(span_rect, card)
            if viol > EDGE_TOLERANCE:
                out.append(Violation(
                    category="card_overflow",
                    page=page_idx + 1,
                    rect_a=tuple(round(v, 2) for v in card),
                    rect_b=tuple(round(v, 2) for v in span_rect),
                    detail=f"text='{span_text[:64]}'",
                    severity_pt=round(viol, 2),
                ))
    return out


def check_block_collision(page, page_idx: int) -> list[Violation]:
    """Block-vs-block overlap. PyMuPDF treats inline math, display equations,
    and theorem environments as separate blocks that legitimately overlap
    their host paragraph. Filters below try to suppress that noise:
        - skip if intersection area > 70% of the smaller block's area
          (smaller block is engulfed; treat as a sub-element)
        - skip if either block is "small" (area < 800 pt^2) — typical of
          single-line math fragments that PyMuPDF splits out
        - skip if both blocks fall inside the same large drawn rect (figure
          region) — internal figure layout is governed by the card-overflow
          check, not this one
    Even with these filters, this check is the noisiest category on math-
    heavy academic PDFs. Treat its output as suggestive, not authoritative.
    """
    out: list[Violation] = []
    blocks = _collect_text_blocks(page)
    links = _collect_link_rects(page)
    figure_rects = [r for r in _collect_card_rects(page) if _rect_area(r) > 5000.0]
    n = len(blocks)
    for i in range(n):
        ra, ta = blocks[i]
        area_a = _rect_area(ra)
        for j in range(i + 1, n):
            rb, tb = blocks[j]
            area_b = _rect_area(rb)
            if not _bbox_intersects(ra, rb):
                continue
            area = _intersect_area(ra, rb)
            if area < BLOCK_OVERLAP_AREA:
                continue
            if _contains(ra, rb) or _contains(rb, ra):
                continue
            smaller = min(area_a, area_b)
            if smaller < 800.0:
                continue
            if smaller > 0 and (area / smaller) > 0.70:
                continue
            inside_same_figure = any(
                _contains(fr, ra, slack=4.0) and _contains(fr, rb, slack=4.0)
                for fr in figure_rects
            )
            if inside_same_figure:
                continue
            if _under_link(ra, links) and _under_link(rb, links):
                continue
            out.append(Violation(
                category="block_collision",
                page=page_idx + 1,
                rect_a=tuple(round(v, 2) for v in ra),
                rect_b=tuple(round(v, 2) for v in rb),
                detail=f"A='{ta[:40]}' | B='{tb[:40]}'",
                severity_pt=round(area, 2),
            ))
    return out


def check_text_image(page, page_idx: int) -> list[Violation]:
    out: list[Violation] = []
    blocks = _collect_text_blocks(page)
    images = _collect_image_rects(page)
    for img in images:
        for blk_rect, blk_text in blocks:
            if not _bbox_intersects(blk_rect, img):
                continue
            area = _intersect_area(blk_rect, img)
            if area < TEXT_IMAGE_OVERLAP_AREA:
                continue
            if _contains(img, blk_rect) or _contains(blk_rect, img):
                continue
            out.append(Violation(
                category="text_image",
                page=page_idx + 1,
                rect_a=tuple(round(v, 2) for v in blk_rect),
                rect_b=tuple(round(v, 2) for v in img),
                detail=f"text='{blk_text[:64]}'",
                severity_pt=round(area, 2),
            ))
    return out


def check_image_image(page, page_idx: int) -> list[Violation]:
    out: list[Violation] = []
    images = _collect_image_rects(page)
    for i in range(len(images)):
        for j in range(i + 1, len(images)):
            a, b = images[i], images[j]
            if not _bbox_intersects(a, b):
                continue
            area = _intersect_area(a, b)
            if area < IMAGE_IMAGE_OVERLAP_AREA:
                continue
            if _contains(a, b) or _contains(b, a):
                continue
            out.append(Violation(
                category="image_image",
                page=page_idx + 1,
                rect_a=tuple(round(v, 2) for v in a),
                rect_b=tuple(round(v, 2) for v in b),
                detail="image vs image",
                severity_pt=round(area, 2),
            ))
    return out


def check_drawing_drawing(page, page_idx: int) -> list[Violation]:
    out: list[Violation] = []
    rects = _collect_card_rects(page)
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            if not _bbox_intersects(a, b):
                continue
            area = _intersect_area(a, b)
            if area < DRAWING_OVERLAP_AREA:
                continue
            if _contains(a, b) or _contains(b, a):
                continue
            # Equal-area touching cards (sibling panels in a grid) tend to
            # share a 1-2pt anti-alias edge but no real area overlap; the
            # area threshold above already absorbs that, so we don't add a
            # special case here.
            out.append(Violation(
                category="drawing_drawing",
                page=page_idx + 1,
                rect_a=tuple(round(v, 2) for v in a),
                rect_b=tuple(round(v, 2) for v in b),
                detail="filled drawing rects overlap",
                severity_pt=round(area, 2),
            ))
    return out


def check_margin_overflow(page, page_idx: int) -> list[Violation]:
    """Body text crossing the NeurIPS body's left or right margin.

    Vertical overflow is intentionally not checked: page numbers, footnote
    rules, and footer marks legitimately appear in the bottom margin band.
    Horizontal overflow is the load-bearing case (wide tables, wide figure
    captions) and is what NeurIPS reviewers flag as a layout violation.
    """
    out: list[Violation] = []
    media = page.mediabox
    body_left = NEURIPS_BODY_LEFT_PT
    body_right = media.x1 - NEURIPS_BODY_RIGHT_INSET_PT
    spans = _collect_text_spans(page)
    for span_rect, span_text in spans:
        left_violation = body_left - span_rect[0]
        right_violation = span_rect[2] - body_right
        worst = max(left_violation, right_violation)
        if worst > MARGIN_TOLERANCE_PT:
            side = "left" if left_violation > right_violation else "right"
            out.append(Violation(
                category="margin_overflow",
                page=page_idx + 1,
                rect_a=(round(body_left, 2), round(media.y0, 2),
                        round(body_right, 2), round(media.y1, 2)),
                rect_b=tuple(round(v, 2) for v in span_rect),
                detail=f"{side} margin; text='{span_text[:64]}'",
                severity_pt=round(worst, 2),
            ))
    return out


CHECK_FUNCS = {
    "card_overflow": check_card_overflow,
    "block_collision": check_block_collision,
    "text_image": check_text_image,
    "image_image": check_image_image,
    "drawing_drawing": check_drawing_drawing,
    "margin_overflow": check_margin_overflow,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdf", default=str(PDF))
    parser.add_argument("--json-out", default=str(DEFAULT_OUT))
    parser.add_argument("--scope", choices=["body", "all"], default="all",
                        help="body = pages 1..9 only; all = full document")
    parser.add_argument("--checks", default=",".join(DEFAULT_CHECKS),
                        help=("comma-separated subset of: "
                              + ",".join(CHECK_CHOICES)
                              + " (block_collision excluded from default; opt-in)"))
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"ERROR: PDF not found at {pdf_path}", file=sys.stderr)
        return 2

    try:
        import fitz
    except ImportError:
        print("ERROR: PyMuPDF (fitz) not installed", file=sys.stderr)
        return 2

    enabled = [c.strip() for c in args.checks.split(",") if c.strip()]
    for c in enabled:
        if c not in CHECK_FUNCS:
            print(f"ERROR: unknown check '{c}'. Choose from: {','.join(CHECK_CHOICES)}",
                  file=sys.stderr)
            return 2

    doc = fitz.open(str(pdf_path))
    page_limit = BODY_PAGE_LIMIT if args.scope == "body" else len(doc)
    pages_scanned = min(page_limit, len(doc))

    violations: list[Violation] = []
    for page_idx in range(pages_scanned):
        page = doc.load_page(page_idx)
        for cname in enabled:
            violations.extend(CHECK_FUNCS[cname](page, page_idx))
    doc.close()

    by_cat: dict[str, int] = {c: 0 for c in enabled}
    for v in violations:
        by_cat[v.category] = by_cat.get(v.category, 0) + 1

    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    out = {
        "_meta": {
            "script": "ci/paper_surface_check.py",
            "script_hash": script_hash,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pdf_path": str(pdf_path),
            "pages_scanned": pages_scanned,
            "scope": args.scope,
            "checks_run": enabled,
            "thresholds": {
                "edge_tolerance_pt": EDGE_TOLERANCE,
                "block_overlap_area_pt2": BLOCK_OVERLAP_AREA,
                "text_image_overlap_area_pt2": TEXT_IMAGE_OVERLAP_AREA,
                "image_image_overlap_area_pt2": IMAGE_IMAGE_OVERLAP_AREA,
                "drawing_overlap_area_pt2": DRAWING_OVERLAP_AREA,
                "containment_tolerance_pt": CONTAINMENT_TOLERANCE,
            },
        },
        "status": "PASS" if not violations else "FAIL",
        "summary": {
            "status": "PASS" if not violations else "FAIL",
            "violation_count": len(violations),
            "by_category": by_cat,
        },
        "violations": [asdict(v) for v in violations],
    }
    Path(args.json_out).write_text(json.dumps(out, indent=2), encoding="utf-8")

    print("=" * 70)
    print("PAPER SURFACE CERT (impacting / containment)")
    print("=" * 70)
    print(f"  pdf:             {pdf_path}")
    print(f"  pages scanned:   {pages_scanned} ({args.scope})")
    print(f"  checks run:      {','.join(enabled)}")
    print(f"  total violations:{len(violations)}")
    if by_cat:
        for c in enabled:
            print(f"    {c:<18} {by_cat.get(c, 0)}")
    if violations:
        print()
        for v in violations[:30]:
            line = f"  [{v.category}] p.{v.page}  sev={v.severity_pt}  {v.detail[:60]}"
            print(line.encode("ascii", "replace").decode("ascii"))
        if len(violations) > 30:
            print(f"  ... +{len(violations) - 30} more (see JSON)")
    print()
    print(f"  Results JSON: {args.json_out}")
    print(f"  Verdict: {out['status']}")
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
