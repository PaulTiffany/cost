"""
certificate_graph.py -- Generate ci/certificate_graph.json, a DAG of cert
relationships.

Node types:  paper_span / claim / data_file / script / figure / check / layer /
             illustration / venue_doc
Edge types:  derives_from / appears_in / rendered_by / checked_by / hashes_to /
             writes_to / binds_to

Exit codes:
  0 - PASS (no orphans)
  1 - orphans found (informative; graph still written)
  2 - invocation / unexpected error

Usage:
  python ci/certificate_graph.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_DIR = REPO_ROOT / "ci"


def _node(node_id: str, node_type: str, **attrs) -> dict:
    n = {"id": node_id, "type": node_type}
    n.update(attrs)
    return n


def _edge(src: str, dst: str, rel: str, **attrs) -> dict:
    e = {"src": src, "dst": dst, "rel": rel}
    e.update(attrs)
    return e


def _nid(path: str) -> str:
    """Stable node ID from a file path (normalised to forward slashes)."""
    return path.replace("\\", "/")


def build_graph() -> dict:
    nodes: dict[str, dict] = {}   # id -> node
    edges: list[dict] = []

    def ensure_node(node_id: str, node_type: str, **attrs):
        nid = _nid(node_id)
        if nid not in nodes:
            nodes[nid] = _node(nid, node_type, **attrs)
        return nid

    # -------------------------------------------------------------------------
    # 1. claim_data_ties.json: claim -> data_file (derives_from)
    # -------------------------------------------------------------------------
    ties_path = CI_DIR / "claim_data_ties.json"
    claim_ids: set[str] = set()
    data_file_ids: set[str] = set()

    if ties_path.exists():
        with ties_path.open("r", encoding="utf-8") as fh:
            ties = json.load(fh)

        for claim_key, claim_data in ties.get("claims", {}).items():
            claim_nid = ensure_node(f"claim:{claim_key}", "claim",
                                    label=claim_key,
                                    excerpt=claim_data.get("claim_text_excerpt", "")[:120])
            claim_ids.add(claim_nid)

            sf = claim_data.get("source_file")
            if sf:
                sf_nid = ensure_node(sf, "data_file", label=Path(sf).name)
                data_file_ids.add(sf_nid)
                edges.append(_edge(claim_nid, sf_nid, "derives_from"))

    # -------------------------------------------------------------------------
    # 2. figure_lineage.json: figure -> script (rendered_by), figure -> data (derives_from)
    # -------------------------------------------------------------------------
    fig_lin_path = CI_DIR / "figure_lineage.json"
    figure_ids: set[str] = set()
    script_ids: set[str] = set()

    if fig_lin_path.exists():
        with fig_lin_path.open("r", encoding="utf-8") as fh:
            fig_lin = json.load(fh)

        for fig_name, fig_data in fig_lin.get("figures", {}).items():
            asset = fig_data.get("asset", fig_name)
            in_use = fig_data.get("in_use", True)
            if "in_use" not in fig_data:
                in_use = fig_data.get("main_tex_ref") is not None

            fig_nid = ensure_node(asset, "figure", label=fig_name, in_use=in_use,
                                  tikz_source=fig_data.get("tikz_source", False))
            figure_ids.add(fig_nid)

            script = fig_data.get("script")
            if script:
                sc_nid = ensure_node(script, "script", label=Path(script).name)
                script_ids.add(sc_nid)
                edges.append(_edge(fig_nid, sc_nid, "rendered_by"))

            for d_path in fig_data.get("data", []):
                d_nid = ensure_node(d_path, "data_file", label=Path(d_path).name)
                data_file_ids.add(d_nid)
                edges.append(_edge(fig_nid, d_nid, "derives_from"))

            for d_path in fig_data.get("outputs_secondary", []):
                d_nid = ensure_node(d_path, "data_file", label=Path(d_path).name)
                data_file_ids.add(d_nid)
                # script writes -> secondary output
                if script:
                    edges.append(_edge(sc_nid, d_nid, "writes_to"))

    # -------------------------------------------------------------------------
    # 3. illustration_lineage.json: illustration -> input file (binds_to)
    # -------------------------------------------------------------------------
    ill_lin_path = CI_DIR / "illustration_lineage.json"
    illustration_ids: set[str] = set()
    venue_doc_ids: set[str] = set()

    if ill_lin_path.exists():
        with ill_lin_path.open("r", encoding="utf-8") as fh:
            ill_lin = json.load(fh)

        for ill_name, ill_data in ill_lin.get("illustrations", {}).items():
            final_asset = ill_data.get("final_asset", ill_name)
            ill_nid = ensure_node(final_asset, "illustration", label=ill_name,
                                  in_use=ill_data.get("in_use", True),
                                  draft_model=ill_data.get("draft_model", ""))
            illustration_ids.add(ill_nid)

            for inp in ill_data.get("inputs", []):
                f = inp.get("file")
                if f:
                    kind = inp.get("kind", "text_file")
                    if kind == "latex_block":
                        src_nid = ensure_node(f, "paper_span",
                                              label=Path(f).name,
                                              line_start=inp.get("line_start"),
                                              line_end=inp.get("line_end"))
                    else:
                        src_nid = ensure_node(f, "data_file", label=Path(f).name)
                        data_file_ids.add(src_nid)
                    edges.append(_edge(ill_nid, src_nid, "binds_to"))

            for vc in ill_data.get("venue_constraints", []):
                f = vc.get("file")
                if f:
                    vc_nid = ensure_node(f, "venue_doc", label=Path(f).name)
                    venue_doc_ids.add(vc_nid)
                    edges.append(_edge(ill_nid, vc_nid, "binds_to",
                                       constraint_kind="venue"))

    # -------------------------------------------------------------------------
    # 4. claim_certificate.json: layers (checks) -> scripts, artifact_hashes
    # -------------------------------------------------------------------------
    cert_path = CI_DIR / "claim_certificate.json"
    check_ids: set[str] = set()

    if cert_path.exists():
        with cert_path.open("r", encoding="utf-8") as fh:
            cert = json.load(fh)

        for layer in cert.get("layers", []):
            layer_name = layer.get("name", "unknown")
            layer_nid = ensure_node(f"layer:{layer_name}", "layer",
                                    label=layer_name,
                                    status=layer.get("status", ""),
                                    return_code=layer.get("return_code"))
            check_ids.add(layer_nid)

            script = layer.get("script")
            if script:
                sc_nid = ensure_node(script, "script", label=Path(script).name)
                script_ids.add(sc_nid)
                edges.append(_edge(layer_nid, sc_nid, "checked_by"))

        for fpath, fhash in cert.get("artifact_hashes", {}).items():
            # layer hashes_to artifact
            # find which layer produced it (by matching script name pattern)
            art_nid = ensure_node(fpath, "data_file", label=Path(fpath).name)
            data_file_ids.add(art_nid)
            # generic: cert hashes this artifact
            cert_nid = ensure_node("cert:claim_certificate.json", "manifest",
                                   label="claim_certificate.json")
            edges.append(_edge(cert_nid, art_nid, "hashes_to", sha256=fhash[:16] + "..."))

        prov = cert.get("provenance", {})
        for key in ("main_tex_path", "main_pdf_path"):
            val = prov.get(key)
            if val:
                role = "paper"
                ensure_node(val, role, label=Path(val).name)

    # -------------------------------------------------------------------------
    # 5. Orphan detection
    # -------------------------------------------------------------------------
    # Data files referenced by no claim
    claimed_data: set[str] = set()
    for e in edges:
        if e["rel"] == "derives_from" and nodes.get(e["src"], {}).get("type") == "claim":
            claimed_data.add(e["dst"])

    orphan_data = [nid for nid in data_file_ids if nid not in claimed_data]

    # Claims with no data
    claims_with_data: set[str] = set()
    for e in edges:
        if e["rel"] == "derives_from" and nodes.get(e["src"], {}).get("type") == "claim":
            claims_with_data.add(e["src"])
    orphan_claims = [nid for nid in claim_ids if nid not in claims_with_data]

    # Figures with no claims (claims referencing figures via appearance)
    # We detect figures that have no incoming derives_from from a claim
    figures_referenced_by_claims: set[str] = set()
    for e in edges:
        if e["rel"] == "appears_in":
            figures_referenced_by_claims.add(e["src"])
    # Also accept: figures with in_use=True are "referenced" by the paper
    orphan_figures = [
        nid for nid in figure_ids
        if nid not in figures_referenced_by_claims
        and not nodes[nid].get("in_use", True)
    ]

    orphans = {
        "data_files": sorted(orphan_data),
        "claims": sorted(orphan_claims),
        "figures": sorted(orphan_figures),
    }

    total_orphans = sum(len(v) for v in orphans.values())

    result = {
        "_meta": {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "n_nodes": len(nodes),
            "n_edges": len(edges),
            "n_orphans": total_orphans,
        },
        "nodes": list(nodes.values()),
        "edges": edges,
        "orphans": orphans,
    }
    return result, total_orphans


def main() -> int:
    try:
        graph, n_orphans = build_graph()

        out_path = CI_DIR / "certificate_graph.json"
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(graph, fh, indent=2)
            fh.write("\n")

        meta = graph["_meta"]
        status = "PASS" if n_orphans == 0 else "WARN"
        print(f"{status}  certificate_graph.json written: "
              f"{meta['n_nodes']} nodes, {meta['n_edges']} edges, "
              f"{n_orphans} orphans")

        if n_orphans > 0:
            orphans = graph["orphans"]
            if orphans["data_files"]:
                print(f"  orphan data_files ({len(orphans['data_files'])}):")
                for p in orphans["data_files"][:10]:
                    print(f"    {p}")
            if orphans["claims"]:
                print(f"  orphan claims ({len(orphans['claims'])}):")
                for c in orphans["claims"][:10]:
                    print(f"    {c}")
            if orphans["figures"]:
                print(f"  orphan figures ({len(orphans['figures'])}):")
                for f in orphans["figures"][:10]:
                    print(f"    {f}")
            return 1

        return 0

    except Exception as exc:
        print(f"ERROR  {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
