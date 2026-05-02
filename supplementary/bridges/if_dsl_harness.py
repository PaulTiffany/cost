#!/usr/bin/env python3
"""
if_dsl_harness.py
=================
Paper: "The Cost of Cacophony" (ICML 2026)
  - main.tex, references.bib

Verifies:
  - Appendix R (app:if_dsl_bridge): IF-DSL domain transfer
  - Claim C23: 0% satisfaction at ρ ≥ 1.0
  - Table A17 (tab:if_dsl): IF-DSL constraint results

ICML-style validation harness: "Diagonal Cost" via Interactive Fiction (IF)
with executable semantics, objective verification, and repair protocol.

Core idea:
  - Ask a model to generate a small IF world (JSON DSL) under multiple constraints.
  - Verify constraints by executing/solving the world with a deterministic interpreter.
  - Operationalize conflict (rho) empirically from a baseline generator distribution:
      rho_ij = 1 - P(i & j) / min(P(i), P(j))  (clipped to [0,1])
  - Measure pass rate vs rho and staging benefit (staged vs one-shot).
  - NEW: Repair protocol generates structured error reports for failed constraints,
    enabling a "staged+repair" condition that amplifies the signal at high rho.

No judge model. No self-report. Objective checks only.

Commands:
  # 1) Baseline: estimate empirical conflict matrix rho
  python supplementary/bridges/if_dsl_harness.py baseline --out supplementary/bridges/out_if_base --samples 3000 --seed 7

  # 2) Make benchmark JSONL template (prompts + blank dsl field)
  python supplementary/bridges/if_dsl_harness.py bench --baseline supplementary/bridges/out_if_base/baseline.json --out supplementary/bridges/out_if_bench

  # 2b) Synthesize filled JSONL (synthetic solver)
  python supplementary/bridges/if_dsl_harness.py synthesize --in supplementary/bridges/out_if_bench/bench_template.jsonl --baseline supplementary/bridges/out_if_base/baseline.json --out supplementary/bridges/out_if_bench

  # 3) Evaluate filled JSONL (each line contains a candidate DSL)
  python supplementary/bridges/if_dsl_harness.py eval --in bench_filled.jsonl --baseline supplementary/bridges/out_if_base/baseline.json --out supplementary/bridges/out_if_eval

  # 4) Generate repair prompts for failed candidates
  python supplementary/bridges/if_dsl_harness.py repair --in bench_filled.jsonl --baseline supplementary/bridges/out_if_base/baseline.json --out supplementary/bridges/out_if_repair

DSL format (JSON):
{
  "meta": {"title":"...", "start_room":"r0"},
  "rooms": [
    {"id":"r0","exits":{"n":"r1"}, "desc":"..."},
    {"id":"r1","exits":{"s":"r0","e":"r2"}, "desc":"..."},
    ...
  ],
  "objects": [
    {"id":"key","location":"r1","portable":true},
    {"id":"door","location":"r1","portable":false,"locked":true,"key":"key","leads_to":"r2"}
  ],
  "goal": {"type":"reach_room","room":"r2"}
}

Actions supported (deterministic):
  - move(dir): if exit exists and not blocked by locked door gating that exit
  - take(obj): if portable and in room
  - unlock(door): if door locked and key in inventory

Outputs:
  - eval_rows.csv / eval_rows.json
  - pass_rate_vs_rho.png
  - staging_benefit_vs_rho.png
  - phase_diagram_{protocol}.png
  - repair_prompts.jsonl (if using repair command)

Author: Research validation harness
"""

from __future__ import annotations
import argparse
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set
import numpy as np
import matplotlib.pyplot as plt

# ----------------------------- 
# IO utils
# ----------------------------- 

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")

def write_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols = list(rows[0].keys())
    lines = [",".join(cols)]
    for r in rows:
        lines.append(",".join(str(r.get(c, "")).replace(",", ";") for c in cols))
    path.write_text("\n".join(lines), encoding="utf-8")

def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x

def read_jsonl(path: Path) -> List[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        out.append(json.loads(s))
    return out

# ----------------------------- 
# DSL validation + execution
# ----------------------------- 

@dataclass(frozen=True)
class World:
    rooms: Dict[str, dict]
    objects: Dict[str, dict]
    start_room: str
    goal: dict

class DSLValidationError(Exception):
    pass

def parse_world(dsl: dict) -> World:
    if not isinstance(dsl, dict):
        raise DSLValidationError("DSL root must be an object.")
    
    meta = dsl.get("meta", {})
    rooms_list = dsl.get("rooms", [])
    objs_list = dsl.get("objects", [])
    goal = dsl.get("goal", {})
    
    if not isinstance(rooms_list, list) or not rooms_list:
        raise DSLValidationError("rooms must be a non-empty list.")
    
    rooms: Dict[str, dict] = {}
    for r in rooms_list:
        rid = r.get("id")
        if not isinstance(rid, str) or not rid:
            raise DSLValidationError("each room needs string id")
        if rid in rooms:
            raise DSLValidationError(f"duplicate room id: {rid}")
        exits = r.get("exits", {})
        if not isinstance(exits, dict):
            raise DSLValidationError(f"room {rid} exits must be object")
        rooms[rid] = {"id": rid, "exits": dict(exits), "desc": str(r.get("desc", ""))}
    
    objects: Dict[str, dict] = {}
    if not isinstance(objs_list, list):
        raise DSLValidationError("objects must be a list")
    
    for o in objs_list:
        oid = o.get("id")
        if not isinstance(oid, str) or not oid:
            raise DSLValidationError("each object needs string id")
        if oid in objects:
            raise DSLValidationError(f"duplicate object id: {oid}")
        loc = o.get("location")
        if loc is not None and loc not in rooms:
            raise DSLValidationError(f"object {oid} location unknown: {loc}")
        portable = bool(o.get("portable", False))
        locked = bool(o.get("locked", False))
        key = o.get("key")
        leads_to = o.get("leads_to")
        
        if leads_to is not None and leads_to not in rooms:
            raise DSLValidationError(f"door {oid} leads_to unknown room: {leads_to}")
        if key is not None and not isinstance(key, str):
            raise DSLValidationError(f"object {oid} key must be string")
        
        objects[oid] = {
            "id": oid,
            "location": loc,
            "portable": portable,
            "locked": locked,
            "key": key,
            "leads_to": leads_to
        }
    
    start_room = str(meta.get("start_room", rooms_list[0].get("id")))
    if start_room not in rooms:
        raise DSLValidationError(f"start_room unknown: {start_room}")
    
    if not isinstance(goal, dict) or "type" not in goal:
        raise DSLValidationError("goal must be object with type")
    
    if goal["type"] == "reach_room":
        room = goal.get("room")
        if room not in rooms:
            raise DSLValidationError("goal room unknown")
    else:
        raise DSLValidationError(f"unsupported goal type: {goal['type']}")
    
    return World(rooms=rooms, objects=objects, start_room=start_room, goal=goal)

@dataclass(frozen=True)
class State:
    room: str
    inventory: Tuple[str, ...]
    locked_doors: Tuple[str, ...]

def initial_state(world: World) -> State:
    locked = tuple(sorted([oid for oid, o in world.objects.items() 
                          if o.get("leads_to") and o.get("locked", False)]))
    return State(room=world.start_room, inventory=tuple(), locked_doors=locked)

def goal_reached(world: World, st: State) -> bool:
    if world.goal["type"] == "reach_room":
        return st.room == world.goal["room"]
    return False

def neighbors(world: World, st: State) -> List[State]:
    nxt: List[State] = []
    inv = set(st.inventory)
    locked = set(st.locked_doors)
    
    # Move actions
    for d, tgt in world.rooms[st.room]["exits"].items():
        blockers = []
        for oid, o in world.objects.items():
            if o.get("leads_to") == tgt and o.get("location") == st.room and oid in locked:
                blockers.append(oid)
        if blockers:
            continue
        nxt.append(State(room=tgt, inventory=st.inventory, locked_doors=st.locked_doors))
    
    # Take actions
    for oid, o in world.objects.items():
        if o.get("portable", False) and o.get("location") == st.room and oid not in inv:
            new_inv = tuple(sorted(list(inv | {oid})))
            nxt.append(State(room=st.room, inventory=new_inv, locked_doors=st.locked_doors))
    
    # Unlock actions
    for oid, o in world.objects.items():
        if oid in locked and o.get("location") == st.room:
            key = o.get("key")
            if isinstance(key, str) and key in inv:
                new_locked = tuple(sorted(list(locked - {oid})))
                nxt.append(State(room=st.room, inventory=st.inventory, locked_doors=new_locked))
    
    return nxt

def bfs_solve(world: World, max_states: int = 20000) -> Tuple[bool, int, int]:
    """
    BFS over discrete state space.
    Returns: solvable, explored_states, shortest_steps_to_goal (or -1)
    """
    start = initial_state(world)
    q = [start]
    dist = {start: 0}
    head = 0
    
    while head < len(q) and len(dist) < max_states:
        st = q[head]
        head += 1
        
        if goal_reached(world, st):
            return True, len(dist), dist[st]
        
        for nb in neighbors(world, st):
            if nb not in dist:
                dist[nb] = dist[st] + 1
                q.append(nb)
    
    return False, len(dist), -1

# ----------------------------- 
# Objective constraints
# ----------------------------- 

@dataclass(frozen=True)
class ConstraintResult:
    name: str
    passed: bool
    score: float
    detail: str = ""  # NEW: for repair prompts

def constraint_connected(world: World) -> ConstraintResult:
    """Room graph connectivity ignoring locks."""
    start = world.start_room
    seen = {start}
    stack = [start]
    
    while stack:
        r = stack.pop()
        for _, t in world.rooms[r]["exits"].items():
            if t in world.rooms and t not in seen:
                seen.add(t)
                stack.append(t)
    
    missing = set(world.rooms.keys()) - seen
    detail = f"Unreachable rooms: {sorted(missing)}" if missing else ""
    s = len(seen) / max(1, len(world.rooms))
    
    return ConstraintResult("connected", 
                           passed=(len(seen) == len(world.rooms)), 
                           score=float(s),
                           detail=detail)

def constraint_exact_rooms(world: World, n: int) -> ConstraintResult:
    actual = len(world.rooms)
    detail = f"Expected {n} rooms, got {actual}"
    s = 1.0 - (abs(actual - n) / max(1, n))
    
    return ConstraintResult(f"rooms_eq_{n}", 
                           passed=(actual == n), 
                           score=clamp01(float(s)),
                           detail=detail if actual != n else "")

def constraint_solvable(world: World) -> ConstraintResult:
    ok, explored, steps = bfs_solve(world)
    detail = "" if ok else f"No solution found after exploring {explored} states"
    
    return ConstraintResult("solvable", 
                           passed=ok, 
                           score=1.0 if ok else 0.0,
                           detail=detail)

def constraint_pathlen_exact(world: World, L: int) -> ConstraintResult:
    ok, explored, steps = bfs_solve(world)
    
    if not ok:
        return ConstraintResult(f"pathlen_eq_{L}", 
                               passed=False, 
                               score=0.0,
                               detail=f"World not solvable (explored {explored} states)")
    
    detail = f"Expected path length {L}, got {steps}" if steps != L else ""
    s = 1.0 - (abs(steps - L) / max(1, L))
    
    return ConstraintResult(f"pathlen_eq_{L}", 
                           passed=(steps == L), 
                           score=clamp01(float(s)),
                           detail=detail)

def count_alt_routes(world: World, start: str, goal: str) -> int:
    """Count distinct simple paths (ignoring locks) up to a cap."""
    cap = 10
    paths = 0
    stack: List[Tuple[str, List[str]]] = [(start, [start])]
    
    while stack and paths < cap:
        node, path = stack.pop()
        if node == goal:
            paths += 1
            continue
        for _, nxt in world.rooms[node]["exits"].items():
            if nxt in path:
                continue
            stack.append((nxt, path + [nxt]))
    
    return paths

def constraint_two_routes(world: World) -> ConstraintResult:
    goal_room = world.goal.get("room")
    if not isinstance(goal_room, str) or goal_room not in world.rooms:
        return ConstraintResult("two_routes", False, 0.0, "Goal room not defined")
    
    k = count_alt_routes(world, world.start_room, goal_room)
    detail = f"Found {k} distinct paths, need >=2" if k < 2 else ""
    s = clamp01((k - 1) / 2.0)
    
    return ConstraintResult("two_routes", 
                           passed=(k >= 2), 
                           score=float(s),
                           detail=detail)

def constraint_one_bottleneck(world: World) -> ConstraintResult:
    """Exactly one articulation point (not start/goal) disconnecting start->goal."""
    goal_room = world.goal.get("room")
    if not isinstance(goal_room, str) or goal_room not in world.rooms:
        return ConstraintResult("one_bottleneck", False, 0.0, "Goal room not defined")
    
    rooms = list(world.rooms.keys())
    adj = {r: set(world.rooms[r]["exits"].values()) for r in rooms}
    
    def reachable(blocked: Optional[str]) -> bool:
        start = world.start_room
        if blocked == start or blocked == goal_room:
            return True
        seen = set()
        stack = [start]
        while stack:
            v = stack.pop()
            if v == blocked:
                continue
            if v == goal_room:
                return True
            if v in seen:
                continue
            seen.add(v)
            for w in adj.get(v, []):
                if w != blocked:
                    stack.append(w)
        return False
    
    candidates = []
    for r in rooms:
        if r in (world.start_room, goal_room):
            continue
        if not reachable(r):
            candidates.append(r)
    
    if len(candidates) == 1:
        return ConstraintResult("one_bottleneck", True, 1.0)
    
    detail = f"Found {len(candidates)} bottlenecks {candidates}, need exactly 1"
    score = 0.2 if len(candidates) == 0 else 0.2 / len(candidates)
    
    return ConstraintResult("one_bottleneck", False, score, detail)

CONSTRAINTS = {
    "connected": constraint_connected,
    "solvable": constraint_solvable,
    "two_routes": constraint_two_routes,
    "one_bottleneck": constraint_one_bottleneck,
}

# ----------------------------- 
# Baseline world generator
# ----------------------------- 

def gen_world_baseline(rng: random.Random, n_rooms: int) -> dict:
    """Generate a small random IF world for empirical rho estimation."""
    rooms = []
    for i in range(n_rooms):
        rooms.append({"id": f"r{i}", "exits": {}, "desc": f"Room {i}."})
    
    # Chain
    for i in range(n_rooms - 1):
        rooms[i]["exits"]["e"] = f"r{i+1}"
        rooms[i+1]["exits"]["w"] = f"r{i}"
    
    # Add 0-2 extra edges for cycles/alt routes
    extra = rng.randint(0, 2)
    for _ in range(extra):
        a = rng.randint(0, n_rooms - 2)
        b = rng.randint(a + 1, n_rooms - 1)
        rooms[a]["exits"][f"x{_}"] = f"r{b}"
        rooms[b]["exits"][f"y{_}"] = f"r{a}"
    
    objects = []
    
    # Optional locked door gating last room
    if rng.random() < 0.6 and n_rooms >= 3:
        door_room = f"r{n_rooms-2}"
        door_id = "door"
        key_id = "key"
        
        objects.append({
            "id": door_id,
            "location": door_room,
            "portable": False,
            "locked": True,
            "key": key_id,
            "leads_to": f"r{n_rooms-1}"
        })
        
        key_loc = f"r{rng.randint(0, max(0, (n_rooms//2)-1))}"
        objects.append({
            "id": key_id,
            "location": key_loc,
            "portable": True
        })
        
        rooms[n_rooms-2]["exits"]["e"] = f"r{n_rooms-1}"
        rooms[n_rooms-1]["exits"]["w"] = f"r{n_rooms-2}"
    
    dsl = {
        "meta": {"title": "baseline", "start_room": "r0"},
        "rooms": rooms,
        "objects": objects,
        "goal": {"type": "reach_room", "room": f"r{n_rooms-1}"},
    }
    
    return dsl

def estimate_conflict_matrix(samples: int, seed: int, n_rooms: int) -> Dict[str, object]:
    """Empirical rho_ij based on baseline generator distribution."""
    rng = random.Random(seed)
    
    names = [
        "connected", 
        "solvable", 
        "two_routes", 
        "one_bottleneck",
        f"rooms_eq_{n_rooms}", 
        f"pathlen_eq_{n_rooms-1}"
    ]
    
    counts = {c: 0 for c in names}
    pair_counts = {(a, b): 0 for i, a in enumerate(names) for b in names[i:]}
    valid = 0
    
    for _ in range(samples):
        dsl = gen_world_baseline(rng, n_rooms=n_rooms)
        try:
            w = parse_world(dsl)
        except Exception:
            continue
        
        valid += 1
        passed: Dict[str, bool] = {}
        
        # Core constraints
        for c in ["connected", "solvable", "two_routes", "one_bottleneck"]:
            res = CONSTRAINTS[c](w)
            passed[c] = res.passed
            if res.passed:
                counts[c] += 1
        
        # Parametrized constraints
        res_r = constraint_exact_rooms(w, n_rooms)
        passed[res_r.name] = res_r.passed
        if res_r.passed:
            counts[res_r.name] += 1
        
        res_L = constraint_pathlen_exact(w, n_rooms - 1)
        passed[res_L.name] = res_L.passed
        if res_L.passed:
            counts[res_L.name] += 1
        
        # Pair counts
        for i, a in enumerate(names):
            for b in names[i:]:
                if passed.get(a, False) and passed.get(b, False):
                    pair_counts[(a, b)] += 1
    
    probs = {c: (counts[c] / valid if valid else 0.0) for c in names}
    rho = {c: {} for c in names}
    
    for i, a in enumerate(names):
        for b in names[i:]:
            pab = pair_counts[(a, b)] / valid if valid else 0.0
            denom = min(probs[a], probs[b]) if min(probs[a], probs[b]) > 0 else 1e-12
            rij = clamp01(1.0 - (pab / denom))
            rho[a][b] = rij
            rho[b][a] = rij
    
    return {
        "valid_samples": valid,
        "constraints": names,
        "marginals": probs,
        "rho": rho,
        "definition": "rho_ij = 1 - P(i&j)/min(P(i),P(j)) over baseline IF generator",
        "baseline_params": {"n_rooms": n_rooms, "seed": seed, "samples": samples},
    }

def set_conflict_score(rho: Dict[str, Dict[str, float]], constraints: List[str]) -> float:
    if len(constraints) < 2:
        return 0.0
    vals = []
    for i, a in enumerate(constraints):
        for b in constraints[i+1:]:
            vals.append(float(rho[a][b]))
    return float(np.mean(vals)) if vals else 0.0

# ----------------------------- 
# Candidate evaluation
# ----------------------------- 

@dataclass(frozen=True)
class Candidate:
    run_id: str
    protocol: str
    constraints: List[str]
    dsl: Optional[dict]

def eval_candidate(c: Candidate, n_rooms: int) -> Dict[str, object]:
    row: Dict[str, object] = {
        "run_id": c.run_id,
        "protocol": c.protocol,
        "constraints": "|".join(c.constraints),
        "k": len(c.constraints),
    }
    
    if not c.dsl:
        row["passed_all"] = 0
        row["error"] = "missing_dsl"
        return row
    
    try:
        w = parse_world(c.dsl)
        
        results: List[ConstraintResult] = []
        for name in c.constraints:
            if name == f"rooms_eq_{n_rooms}":
                results.append(constraint_exact_rooms(w, n_rooms))
            elif name == f"pathlen_eq_{n_rooms-1}":
                results.append(constraint_pathlen_exact(w, n_rooms - 1))
            else:
                if name not in CONSTRAINTS:
                    results.append(ConstraintResult(name, False, 0.0, "Unknown constraint"))
                else:
                    results.append(CONSTRAINTS[name](w))
        
        passed_all = all(r.passed for r in results)
        row["passed_all"] = int(passed_all)
        row["scores"] = json.dumps({r.name: r.score for r in results})
        row["passes"] = json.dumps({r.name: r.passed for r in results})
        row["failures"] = json.dumps({r.name: r.detail for r in results if not r.passed})
        
        ok, explored, steps = bfs_solve(w)
        row["solvable_bool"] = int(ok)
        row["states_explored"] = explored
        row["shortest_steps"] = steps
        row["n_rooms"] = len(w.rooms)
        row["n_objects"] = len(w.objects)
        
        return row
        
    except Exception as e:
        row["passed_all"] = 0
        row["error"] = str(e)
        return row

def _coerce_constraints(raw: object) -> List[str]:
    if isinstance(raw, list):
        return [str(c) for c in raw]
    if isinstance(raw, str):
        return [c for c in raw.split("|") if c]
    return []

def synthesize_dsl(
    constraints: List[str],
    n_rooms: int,
    rng: random.Random,
    max_attempts: int,
    run_id: str,
    protocol: str
) -> Tuple[dict, int, int]:
    """Dispatch to protocol-specific synthesis."""
    if protocol == "staged":
        return synthesize_staged(constraints, n_rooms, rng, max_attempts, run_id)
    else:
        return synthesize_one_shot(constraints, n_rooms, rng, max_attempts, run_id)


# NOTE: synthesize_one_shot and synthesize_staged are defined after repair operators


# -----------------------------
# Repair-directed edit operators
# -----------------------------

def deep_copy_dsl(dsl: dict) -> dict:
    """Deep copy a DSL structure."""
    return json.loads(json.dumps(dsl))


def get_adj_digraph(world: World) -> Dict[str, Set[str]]:
    """Get directed adjacency from room exits."""
    return {r: set(world.rooms[r]["exits"].values()) for r in world.rooms}


def get_reverse_adj(adj: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
    """Get reverse adjacency (for backward reachability)."""
    rev: Dict[str, Set[str]] = {r: set() for r in adj}
    for r, neighbors in adj.items():
        for n in neighbors:
            if n in rev:
                rev[n].add(r)
    return rev


def bfs_reachable_set(start: str, adj: Dict[str, Set[str]], blocked: Optional[str] = None) -> Set[str]:
    """BFS reachability from start, optionally blocking one node."""
    if start == blocked:
        return set()
    seen = {start}
    queue = [start]
    head = 0
    while head < len(queue):
        v = queue[head]
        head += 1
        for w in adj.get(v, []):
            if w != blocked and w not in seen:
                seen.add(w)
                queue.append(w)
    return seen


def shortest_room_path(start: str, goal: str, adj: Dict[str, Set[str]]) -> Optional[List[str]]:
    """BFS shortest path from start to goal on room digraph."""
    if start == goal:
        return [start]
    parent: Dict[str, str] = {start: ""}
    queue = [start]
    head = 0
    while head < len(queue):
        v = queue[head]
        head += 1
        for w in adj.get(v, []):
            if w not in parent:
                parent[w] = v
                if w == goal:
                    # Reconstruct path
                    path = [w]
                    while parent[path[-1]]:
                        path.append(parent[path[-1]])
                    return list(reversed(path))
                queue.append(w)
    return None


def find_unused_direction(room: dict, preferred: List[str] = None) -> Optional[str]:
    """Find an unused exit direction for a room."""
    all_dirs = preferred or ["n", "s", "e", "w", "ne", "nw", "se", "sw", "u", "d"]
    used = set(room.get("exits", {}).keys())
    for d in all_dirs:
        if d not in used:
            return d
    return None


def add_exit_to_dsl(dsl: dict, from_room: str, to_room: str, direction: Optional[str] = None) -> bool:
    """Add a directed exit from one room to another."""
    rooms = dsl.get("rooms", [])
    for r in rooms:
        if r["id"] == from_room:
            if direction is None:
                direction = find_unused_direction(r)
            if direction:
                r["exits"][direction] = to_room
                return True
    return False


def remove_exit_from_dsl(dsl: dict, from_room: str, to_room: str) -> bool:
    """Remove a directed exit from one room to another."""
    rooms = dsl.get("rooms", [])
    for r in rooms:
        if r["id"] == from_room:
            exits = r.get("exits", {})
            for d, target in list(exits.items()):
                if target == to_room:
                    del exits[d]
                    return True
    return False


# --- Operator A: one_bottleneck ---

def diagnose_bottleneck(world: World) -> Tuple[List[str], str, str]:
    """Return (candidates, start, goal) for bottleneck constraint."""
    goal_room = world.goal.get("room", "")
    if not isinstance(goal_room, str) or goal_room not in world.rooms:
        return [], world.start_room, ""

    rooms = list(world.rooms.keys())
    adj = get_adj_digraph(world)

    candidates = []
    for r in rooms:
        if r in (world.start_room, goal_room):
            continue
        reachable = bfs_reachable_set(world.start_room, adj, blocked=r)
        if goal_room not in reachable:
            candidates.append(r)

    return candidates, world.start_room, goal_room


def op_make_single_articulation(dsl: dict, rng: random.Random) -> dict:
    """
    Operator A1: Create exactly one bottleneck when candidates==0.
    Partition rooms and funnel all start→goal traffic through one room.
    """
    dsl = deep_copy_dsl(dsl)
    try:
        world = parse_world(dsl)
    except:
        return dsl

    candidates, start, goal = diagnose_bottleneck(world)
    if len(candidates) == 1:
        return dsl  # Already satisfied

    rooms = list(world.rooms.keys())
    if len(rooms) < 3:
        return dsl

    # Pick a bottleneck candidate (not start/goal, prefer middle of chain)
    middle_candidates = [r for r in rooms if r not in (start, goal)]
    if not middle_candidates:
        return dsl

    # Choose bottleneck b (prefer one that's already somewhat central)
    adj = get_adj_digraph(world)
    path = shortest_room_path(start, goal, adj)
    if path and len(path) >= 3:
        # Pick a room from the middle of the path
        b = path[len(path) // 2]
    else:
        b = rng.choice(middle_candidates)

    if b == start or b == goal:
        b = rng.choice(middle_candidates)

    # Partition: A = rooms reachable from start without going through b
    # B = rooms from which goal is reachable without going through b
    A = bfs_reachable_set(start, adj, blocked=b) - {b}
    rev_adj = get_reverse_adj(adj)
    B = bfs_reachable_set(goal, rev_adj, blocked=b) - {b}

    # Ensure start in A, goal in B
    A.add(start)
    B.add(goal)
    A.discard(goal)
    B.discard(start)

    # Remove all A→B edges except those going to/from b
    rooms_list = dsl.get("rooms", [])
    for r in rooms_list:
        rid = r["id"]
        if rid in A and rid != b:
            exits = r.get("exits", {})
            for d, target in list(exits.items()):
                if target in B and target != b:
                    del exits[d]

    # Ensure connectivity: A → b → B
    # Add edge from some room in A to b
    a_rooms = [r for r in rooms_list if r["id"] in A and r["id"] != b]
    if a_rooms:
        a_room = rng.choice(a_rooms)
        add_exit_to_dsl(dsl, a_room["id"], b)

    # Add edge from b to some room in B (preferably goal or near goal)
    b_room = next((r for r in rooms_list if r["id"] == b), None)
    if b_room:
        b_targets = [r for r in rooms_list if r["id"] in B]
        if b_targets:
            target = rng.choice(b_targets)
            add_exit_to_dsl(dsl, b, target["id"])

    return dsl


def op_bypass_bottleneck(dsl: dict, rng: random.Random, keep: Optional[str] = None) -> dict:
    """
    Operator A2: Reduce bottlenecks when candidates>1.
    Add bypass edges to eliminate extra bottlenecks while keeping one.
    """
    dsl = deep_copy_dsl(dsl)
    try:
        world = parse_world(dsl)
    except:
        return dsl

    candidates, start, goal = diagnose_bottleneck(world)
    if len(candidates) <= 1:
        return dsl  # Already good

    adj = get_adj_digraph(world)

    # Pick one to keep (prefer one that's most "central")
    if keep is None:
        path = shortest_room_path(start, goal, adj)
        if path:
            for r in path:
                if r in candidates:
                    keep = r
                    break
        if keep is None:
            keep = candidates[0]

    # For each other candidate, add a bypass
    for r in candidates:
        if r == keep:
            continue

        # Find u reachable from start without r, and v that can reach goal without r
        reachable_from_start = bfs_reachable_set(start, adj, blocked=r)
        rev_adj = get_reverse_adj(adj)
        can_reach_goal = bfs_reachable_set(goal, rev_adj, blocked=r)

        # Pick u from reachable_from_start (not r, not goal)
        u_candidates = [x for x in reachable_from_start if x != r and x != goal]
        # Pick v from can_reach_goal (not r, not start)
        v_candidates = [x for x in can_reach_goal if x != r and x != start]

        if u_candidates and v_candidates:
            u = rng.choice(u_candidates)
            v = rng.choice(v_candidates)
            add_exit_to_dsl(dsl, u, v)

    return dsl


# --- Operator B: two_routes ---

def diagnose_routes(world: World) -> Tuple[int, Optional[List[str]]]:
    """Return (route_count, one_path) for two_routes constraint."""
    goal_room = world.goal.get("room", "")
    if not isinstance(goal_room, str) or goal_room not in world.rooms:
        return 0, None

    k = count_alt_routes(world, world.start_room, goal_room)
    adj = get_adj_digraph(world)
    path = shortest_room_path(world.start_room, goal_room, adj)
    return k, path


def op_add_path_chord(dsl: dict, rng: random.Random) -> dict:
    """
    Operator B1: Add an alternate route chord.
    Find existing path and add a shortcut creating an alternate route.
    """
    dsl = deep_copy_dsl(dsl)
    try:
        world = parse_world(dsl)
    except:
        return dsl

    k, path = diagnose_routes(world)
    if path is None or len(path) < 3:
        return dsl

    # Choose two nodes p_i, p_j with j >= i+2
    if len(path) >= 4:
        i = rng.randint(0, len(path) - 3)
        j = rng.randint(i + 2, len(path) - 1)
        add_exit_to_dsl(dsl, path[i], path[j])

    return dsl


def op_route_via_offpath(dsl: dict, rng: random.Random) -> dict:
    """
    Operator B2: Add a parallel branch via an off-path room.
    """
    dsl = deep_copy_dsl(dsl)
    try:
        world = parse_world(dsl)
    except:
        return dsl

    k, path = diagnose_routes(world)
    if path is None or len(path) < 2:
        return dsl

    # Find off-path rooms
    path_set = set(path)
    off_path = [r for r in world.rooms if r not in path_set]

    if not off_path:
        return dsl

    x = rng.choice(off_path)

    # Connect p_i -> x -> p_j for some i < j
    if len(path) >= 3:
        i = rng.randint(0, len(path) - 2)
        j = rng.randint(i + 1, len(path) - 1)
        add_exit_to_dsl(dsl, path[i], x)
        add_exit_to_dsl(dsl, x, path[j])
    else:
        # Just connect start -> x -> goal
        add_exit_to_dsl(dsl, path[0], x)
        add_exit_to_dsl(dsl, x, path[-1])

    return dsl


# --- Operator C: pathlen_eq_L ---

def diagnose_pathlen(world: World) -> Tuple[bool, int]:
    """Return (solvable, steps) for pathlen constraint."""
    ok, _, steps = bfs_solve(world)
    return ok, steps


def op_lengthen_path(dsl: dict, rng: random.Random) -> dict:
    """
    Operator C1: Lengthen the solution path when steps < L.
    Remove a shortcut or add a detour.
    """
    dsl = deep_copy_dsl(dsl)
    try:
        world = parse_world(dsl)
    except:
        return dsl

    adj = get_adj_digraph(world)
    goal = world.goal.get("room", "")
    path = shortest_room_path(world.start_room, goal, adj)

    if path is None or len(path) < 2:
        return dsl

    # Strategy: redirect an edge on the path through a detour room
    # Find off-path room to use as detour
    path_set = set(path)
    off_path = [r for r in world.rooms if r not in path_set]

    if off_path and len(path) >= 2:
        # Pick an edge to detour: path[i] -> path[i+1]
        i = rng.randint(0, len(path) - 2)
        x = rng.choice(off_path)

        # Remove direct edge, add detour
        remove_exit_from_dsl(dsl, path[i], path[i + 1])
        add_exit_to_dsl(dsl, path[i], x)
        add_exit_to_dsl(dsl, x, path[i + 1])

    return dsl


def op_shorten_path(dsl: dict, rng: random.Random) -> dict:
    """
    Operator C2: Shorten the solution path when steps > L.
    Add a shortcut toward goal.
    """
    dsl = deep_copy_dsl(dsl)
    try:
        world = parse_world(dsl)
    except:
        return dsl

    adj = get_adj_digraph(world)
    goal = world.goal.get("room", "")
    path = shortest_room_path(world.start_room, goal, adj)

    if path is None or len(path) < 3:
        return dsl

    # Add shortcut: path[i] -> path[j] for j >= i+2
    i = rng.randint(0, len(path) - 3)
    j = rng.randint(i + 2, len(path) - 1)
    add_exit_to_dsl(dsl, path[i], path[j])

    return dsl


# --- Unified repair dispatcher ---

def select_repair_operator(
    world: World,
    constraint: str,
    n_rooms: int,
    rng: random.Random
) -> Optional[callable]:
    """
    Select a repair-directed operator based on constraint diagnostics.
    Returns a callable that takes (dsl, rng) -> dsl.
    """
    if constraint == "one_bottleneck":
        candidates, _, _ = diagnose_bottleneck(world)
        if len(candidates) == 0:
            return op_make_single_articulation
        elif len(candidates) > 1:
            return op_bypass_bottleneck
        return None

    elif constraint == "two_routes":
        k, _ = diagnose_routes(world)
        if k < 2:
            # Randomly choose between chord and offpath
            return rng.choice([op_add_path_chord, op_route_via_offpath])
        return None

    elif constraint.startswith("pathlen_eq_"):
        L = int(constraint.split("_")[-1])
        ok, steps = diagnose_pathlen(world)
        if not ok:
            return None  # Can't fix unsolvable here
        if steps < L:
            return op_lengthen_path
        elif steps > L:
            return op_shorten_path
        return None

    # Fallback: no targeted operator
    return None


def apply_repair_edit(dsl: dict, constraint: str, n_rooms: int, rng: random.Random) -> dict:
    """Apply a repair-directed edit for a specific constraint."""
    try:
        world = parse_world(dsl)
        op = select_repair_operator(world, constraint, n_rooms, rng)
        if op:
            return op(dsl, rng)
    except:
        pass
    return dsl


def apply_random_edit(dsl: dict, rng: random.Random) -> dict:
    """Apply a random structural edit (fallback when no targeted repair)."""
    dsl = deep_copy_dsl(dsl)
    rooms = dsl.get("rooms", [])

    if len(rooms) < 2:
        return dsl

    edit_type = rng.choice(["add_exit", "remove_exit", "swap_exit"])

    if edit_type == "add_exit":
        i, j = rng.sample(range(len(rooms)), 2)
        add_exit_to_dsl(dsl, rooms[i]["id"], rooms[j]["id"])

    elif edit_type == "remove_exit":
        r = rng.choice(rooms)
        exits = r.get("exits", {})
        if len(exits) > 1:
            d = rng.choice(list(exits.keys()))
            del exits[d]

    elif edit_type == "swap_exit":
        r = rng.choice(rooms)
        exits = r.get("exits", {})
        if exits:
            d = rng.choice(list(exits.keys()))
            new_target = rng.choice(rooms)["id"]
            exits[d] = new_target

    return dsl


def check_constraint(w: World, constraint: str, n_rooms: int) -> bool:
    """Check if a single constraint passes."""
    try:
        if constraint.startswith("rooms_eq_"):
            return constraint_exact_rooms(w, n_rooms).passed
        elif constraint.startswith("pathlen_eq_"):
            L = int(constraint.split("_")[-1])
            return constraint_pathlen_exact(w, L).passed
        elif constraint in CONSTRAINTS:
            return CONSTRAINTS[constraint](w).passed
    except:
        pass
    return False


# -----------------------------
# Shared search primitive + synthesis protocols
# -----------------------------

def score_candidate(dsl: dict, constraints: List[str], n_rooms: int) -> Tuple[int, float, Dict[str, bool]]:
    """
    Score a candidate DSL against constraints.
    Returns: (pass_count, total_score, pass_dict)
    """
    try:
        w = parse_world(dsl)
    except:
        return 0, 0.0, {}

    passes: Dict[str, bool] = {}
    scores: Dict[str, float] = {}

    for c in constraints:
        if c.startswith("rooms_eq_"):
            res = constraint_exact_rooms(w, n_rooms)
        elif c.startswith("pathlen_eq_"):
            L = int(c.split("_")[-1])
            res = constraint_pathlen_exact(w, L)
        elif c in CONSTRAINTS:
            res = CONSTRAINTS[c](w)
        else:
            res = ConstraintResult(c, False, 0.0, "Unknown")

        passes[c] = res.passed
        scores[c] = res.score

    pass_count = sum(1 for v in passes.values() if v)
    total_score = sum(scores.values())
    return pass_count, total_score, passes


def edit_search_step(
    dsl: dict,
    target_constraints: List[str],
    preserve_constraints: List[str],
    n_rooms: int,
    rng: random.Random,
    use_repair: bool = True
) -> Tuple[dict, bool]:
    """
    SHARED SEARCH PRIMITIVE: One edit step with optional repair-direction.

    Both protocols use this same primitive. The difference is:
    - one_shot: target_constraints = ALL, preserve_constraints = []
    - staged: target_constraints = [current], preserve_constraints = [earlier...]

    Returns: (new_dsl, improved)
    """
    # First, try repair-directed edit for target constraints
    candidate = dsl
    if use_repair and target_constraints:
        # Pick a failing target constraint and apply targeted repair
        try:
            w = parse_world(dsl)
            for tc in target_constraints:
                if not check_constraint(w, tc, n_rooms):
                    op = select_repair_operator(w, tc, n_rooms, rng)
                    if op:
                        candidate = op(dsl, rng)
                        break
            else:
                # All targets pass, apply random edit
                candidate = apply_random_edit(dsl, rng)
        except:
            candidate = apply_random_edit(dsl, rng)
    else:
        candidate = apply_random_edit(dsl, rng)

    # Check if candidate is valid
    try:
        w_new = parse_world(candidate)
    except:
        return dsl, False

    # Must preserve all earlier constraints
    for pc in preserve_constraints:
        if not check_constraint(w_new, pc, n_rooms):
            return dsl, False  # Reject: breaks preservation

    # Score improvement on targets
    old_pass, old_score, _ = score_candidate(dsl, target_constraints, n_rooms)
    new_pass, new_score, _ = score_candidate(candidate, target_constraints, n_rooms)

    # Accept if strictly better or equal (allows exploration)
    if new_pass > old_pass or (new_pass == old_pass and new_score >= old_score):
        return candidate, (new_pass > old_pass)

    return dsl, False


def synthesize_one_shot(
    constraints: List[str],
    n_rooms: int,
    rng: random.Random,
    budget: int,
    run_id: str
) -> Tuple[dict, int, int]:
    """
    ONE-SHOT PROTOCOL: Optimize ALL constraints jointly under budget B.

    EVALUATION CONTRACT:
    - Total budget = B edit operations
    - Must satisfy all constraints simultaneously
    - Uses repair-directed edits targeting any failing constraint
    - No incremental locking of constraints

    This represents the "diagonal move" in constraint space.
    """
    if not constraints:
        return gen_world_baseline(rng, n_rooms=n_rooms), 0, 0

    # Start with random baseline
    current_dsl = gen_world_baseline(rng, n_rooms=n_rooms)
    best_dsl = current_dsl
    best_pass, best_score, _ = score_candidate(current_dsl, constraints, n_rooms)

    if best_pass == len(constraints):
        return current_dsl, 1, len(constraints)

    attempts = 1
    stagnant = 0
    max_stagnant = budget // 4  # Restart if stuck

    while attempts < budget:
        attempts += 1

        # Use shared search primitive: target ALL, preserve NONE
        new_dsl, improved = edit_search_step(
            current_dsl,
            target_constraints=constraints,
            preserve_constraints=[],
            n_rooms=n_rooms,
            rng=rng,
            use_repair=True
        )

        current_dsl = new_dsl
        curr_pass, curr_score, passes = score_candidate(current_dsl, constraints, n_rooms)

        # Track best
        if curr_pass > best_pass or (curr_pass == best_pass and curr_score > best_score):
            best_dsl = current_dsl
            best_pass = curr_pass
            best_score = curr_score
            stagnant = 0
        else:
            stagnant += 1

        # Early success
        if curr_pass == len(constraints):
            return current_dsl, attempts, len(constraints)

        # Restart if stagnant
        if stagnant > max_stagnant:
            current_dsl = gen_world_baseline(rng, n_rooms=n_rooms)
            stagnant = 0

    return best_dsl, attempts, best_pass


def synthesize_staged(
    constraints: List[str],
    n_rooms: int,
    rng: random.Random,
    budget: int,
    run_id: str
) -> Tuple[dict, int, int]:
    """
    STAGED PROTOCOL: Satisfy constraints SEQUENTIALLY with preservation.

    EVALUATION CONTRACT (AMORTIZED):
    - Total budget = k * B (where k = number of constraints)
    - Each stage gets budget B to satisfy ONE new constraint
    - MUST preserve all previously satisfied constraints
    - Uses repair-directed edits targeting the current constraint

    This represents k "axis-aligned moves" in constraint space.
    The key insight: at high rho, the feasible region shrinks with each
    constraint, so staged can navigate the intersection more efficiently
    by committing to partial solutions.

    EXPLICIT BUDGET ACCOUNTING:
    - Stage i: B attempts to satisfy C_i while preserving {C_1...C_{i-1}}
    - Total compute: k * B (clearly stated, not hidden)
    """
    k = len(constraints)
    if k == 0:
        return gen_world_baseline(rng, n_rooms=n_rooms), 0, 0

    budget_per_stage = budget  # Each stage gets full budget B
    total_attempts = 0
    current_dsl = None
    satisfied: List[str] = []

    for stage_idx, constraint in enumerate(constraints):
        target = [constraint]
        preserve = satisfied.copy()
        found = False

        # Stage 0: Start from random baseline
        if stage_idx == 0:
            current_dsl = gen_world_baseline(rng, n_rooms=n_rooms)

            # Check if already satisfied
            try:
                w = parse_world(current_dsl)
                if check_constraint(w, constraint, n_rooms):
                    satisfied.append(constraint)
                    total_attempts += 1
                    found = True
            except:
                pass

        if not found and current_dsl is not None:
            stagnant = 0
            max_stagnant = budget_per_stage // 4

            for attempt in range(budget_per_stage):
                total_attempts += 1

                # Use shared search primitive: target CURRENT, preserve EARLIER
                new_dsl, improved = edit_search_step(
                    current_dsl,
                    target_constraints=target,
                    preserve_constraints=preserve,
                    n_rooms=n_rooms,
                    rng=rng,
                    use_repair=True
                )

                current_dsl = new_dsl

                # Check if current constraint now satisfied
                try:
                    w = parse_world(current_dsl)
                    if check_constraint(w, constraint, n_rooms):
                        # Verify preservation
                        all_preserved = all(
                            check_constraint(w, pc, n_rooms) for pc in preserve
                        )
                        if all_preserved:
                            satisfied.append(constraint)
                            found = True
                            break
                except:
                    pass

                if not improved:
                    stagnant += 1
                else:
                    stagnant = 0

                # If completely stuck, try restart preserving earlier constraints
                if stagnant > max_stagnant:
                    for _ in range(10):
                        candidate = gen_world_baseline(rng, n_rooms=n_rooms)
                        try:
                            w = parse_world(candidate)
                            if all(check_constraint(w, pc, n_rooms) for pc in preserve):
                                current_dsl = candidate
                                break
                        except:
                            continue
                    stagnant = 0

        # If still no valid DSL, create baseline
        if current_dsl is None:
            current_dsl = gen_world_baseline(rng, n_rooms=n_rooms)

    # Final evaluation
    final_pass, _, passes = score_candidate(current_dsl, constraints, n_rooms)
    return current_dsl, total_attempts, final_pass

# ----------------------------- 
# Plotting
# ----------------------------- 

def plot_pass_vs_rho(rows: List[Dict[str, object]], outdir: Path) -> None:
    def rho_bin(r: float) -> float:
        return round(r / 0.1) * 0.1
    
    agg: Dict[Tuple[str, float], List[int]] = {}
    for r in rows:
        if "rho_set" not in r:
            continue
        key = (str(r["protocol"]), rho_bin(float(r["rho_set"])))
        agg.setdefault(key, []).append(int(r["passed_all"]))
    
    pts = sorted(agg.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    if not pts:
        return
    
    plt.figure(figsize=(8, 5))
    for proto in sorted(set(k[0] for k, _ in pts)):
        xs, ys = [], []
        for (p, rb), vals in pts:
            if p == proto:
                xs.append(rb)
                ys.append(float(np.mean(vals)))
        plt.plot(xs, ys, marker="o", label=proto, linewidth=2)
    
    plt.xlabel("Empirical conflict rho (binned)", fontsize=11)
    plt.ylabel("Pass rate (all constraints)", fontsize=11)
    plt.title("Pass rate vs conflict (rho) - IF DSL", fontsize=12)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(outdir / "pass_rate_vs_rho.png", dpi=200)
    plt.close()

def plot_staging_benefit(rows: List[Dict[str, object]], outdir: Path) -> None:
    by_key: Dict[Tuple[str, float], Dict[str, List[int]]] = {}
    
    for r in rows:
        if "rho_set" not in r:
            continue
        key = (str(r["constraints"]), float(r["rho_set"]))
        by_key.setdefault(key, {}).setdefault(str(r["protocol"]), []).append(int(r["passed_all"]))
    
    xs, ys = [], []
    for (cset, rho), d in by_key.items():
        if "staged" in d and "one_shot" in d:
            ps = float(np.mean(d["staged"]))
            po = float(np.mean(d["one_shot"]))
            xs.append(rho)
            ys.append(ps - po)
    
    if not xs:
        return
    
    plt.figure(figsize=(8, 5))
    plt.scatter(xs, ys, alpha=0.6)
    plt.axhline(0, color='gray', linestyle='--', alpha=0.5)
    plt.xlabel("Empirical conflict rho (set)", fontsize=11)
    plt.ylabel("Staging benefit delta(pass rate)", fontsize=11)
    plt.title("Staging benefit vs conflict (rho) - IF DSL", fontsize=12)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(outdir / "staging_benefit_vs_rho.png", dpi=200)
    plt.close()

def plot_phase_diagram(rows: List[Dict[str, object]], outdir: Path) -> None:
    def bin_rho(r: float) -> float:
        return round(r / 0.1) * 0.1
    
    for proto in sorted(set(str(r.get("protocol", "")) for r in rows)):
        filt = [r for r in rows if str(r.get("protocol", "")) == proto and "rho_set" in r]
        if not filt:
            continue
        
        rhos = sorted(set(bin_rho(float(r["rho_set"])) for r in filt))
        ks = sorted(set(int(r["k"]) for r in filt))
        
        grid = np.full((len(ks), len(rhos)), np.nan, dtype=np.float32)
        
        for i, k in enumerate(ks):
            for j, rb in enumerate(rhos):
                vals = [int(r["passed_all"]) for r in filt 
                       if int(r["k"]) == k and bin_rho(float(r["rho_set"])) == rb]
                if vals:
                    grid[i, j] = float(np.mean(vals))
        
        plt.figure(figsize=(8, 6))
        plt.imshow(grid, aspect="auto", origin="lower", cmap="RdYlGn", vmin=0, vmax=1)
        plt.xticks(range(len(rhos)), [f"{x:.1f}" for x in rhos])
        plt.yticks(range(len(ks)), ks)
        plt.xlabel("Conflict rho (binned)", fontsize=11)
        plt.ylabel("k constraints", fontsize=11)
        plt.title(f"Phase diagram (pass rate): {proto} - IF DSL", fontsize=12)
        plt.colorbar(label="pass rate")
        plt.tight_layout()
        plt.savefig(outdir / f"phase_diagram_{proto}.png", dpi=200)
        plt.close()

# ----------------------------- 
# Repair protocol (NEW)
# ----------------------------- 

def generate_repair_prompt(candidate: Candidate, eval_row: Dict[str, object], n_rooms: int) -> Optional[str]:
    """Generate structured repair prompt from constraint failures."""
    if int(eval_row.get("passed_all", 0)) == 1:
        return None  # Already passed
    
    failures_json = eval_row.get("failures", "{}")
    try:
        failures = json.loads(failures_json) if isinstance(failures_json, str) else failures_json
    except:
        failures = {}
    
    if not failures:
        return None
    
    # Build repair guidance
    repair_lines = [
        "The previous DSL failed these constraints:",
        ""
    ]
    
    for name, detail in failures.items():
        desc = describe_constraint(name, n_rooms)
        repair_lines.append(f"FAIL {name}: {desc}")
        if detail:
            repair_lines.append(f"   Issue: {detail}")
        repair_lines.append("")
    
    repair_lines.extend([
        "REPAIR INSTRUCTIONS:",
        "1. Keep the basic structure (room ids, object ids) intact where possible",
        "2. Fix ONLY what's broken - don't over-engineer",
        "3. Preserve what's working (check passed constraints)",
        "",
        "Passed constraints (preserve these):",
    ])
    
    passes_json = eval_row.get("passes", "{}")
    try:
        passes = json.loads(passes_json) if isinstance(passes_json, str) else passes_json
    except:
        passes = {}
    
    for name, passed in passes.items():
        if passed:
            repair_lines.append(f"OK {name}")
    
    repair_lines.extend([
        "",
        "Return ONLY the repaired JSON DSL. No explanation."
    ])
    
    return "\n".join(repair_lines)

# ----------------------------- 
# Prompts
# ----------------------------- 

def describe_constraint(name: str, n_rooms: int) -> str:
    if name == "connected":
        return "All rooms reachable from start in the room graph (ignoring locks)."
    if name == "solvable":
        return "There exists a sequence of actions to reach the goal."
    if name == "two_routes":
        return "At least two distinct simple room-graph paths start->goal (ignoring locks)."
    if name == "one_bottleneck":
        return "Exactly one articulation room (not start/goal) whose removal disconnects start->goal."
    if name == f"rooms_eq_{n_rooms}":
        return f"World has exactly {n_rooms} rooms."
    if name == f"pathlen_eq_{n_rooms-1}":
        return f"Shortest solvable action path to goal is exactly {n_rooms-1} steps."
    return "Unknown constraint."

def make_prompt_one_shot(constraints: List[str], n_rooms: int) -> str:
    cdesc = "\n".join(f"- {c}: {describe_constraint(c, n_rooms)}" for c in constraints)
    return (
        "Return ONLY a valid JSON object in the IF DSL described below. No explanation.\n\n"
        "IF DSL requirements:\n"
        "- meta.start_room is an existing room id.\n"
        "- rooms is a list of {id, exits, desc}. exits maps directions to room ids.\n"
        "- objects is a list of {id, location, portable, locked?, key?, leads_to?}.\n"
        "- goal is {type:'reach_room', room:}.\n"
        "- Optional: include one locked door object (leads_to + locked + key) and a key object.\n\n"
        "Constraints to satisfy:\n"
        f"{cdesc}\n\n"
        "Return JSON only."
    )

def make_prompt_staged(constraints: List[str], n_rooms: int) -> str:
    return (
        "We will build an IF DSL JSON world in stages. At each stage, return ONLY the full JSON.\n"
        "Goal: satisfy all constraints while preserving earlier structure.\n\n"
        f"Target constraints:\n" +
        "\n".join(f"- {c}: {describe_constraint(c, n_rooms)}" for c in constraints) +
        "\n\nStaging plan:\n"
        "1) Build the room graph (rooms + exits) first to satisfy structural/topology constraints.\n"
        "2) Add goal room + ensure reachability.\n"
        "3) Add objects/locks (door/key) if needed to satisfy solvability/path-length constraints.\n"
        "4) Add desc fields last; do not break ids/exits.\n\n"
        "Return JSON only."
    )

# ----------------------------- 
# Commands
# ----------------------------- 

def cmd_baseline(args: argparse.Namespace) -> None:
    outdir = Path(args.out)
    ensure_dir(outdir)
    
    baseline = estimate_conflict_matrix(
        samples=args.samples,
        seed=args.seed,
        n_rooms=args.n_rooms
    )
    
    write_json(outdir / "baseline.json", baseline)
    print(f"[baseline] wrote {outdir/'baseline.json'} (valid_samples={baseline['valid_samples']})")

def cmd_bench(args: argparse.Namespace) -> None:
    outdir = Path(args.out)
    ensure_dir(outdir)
    
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    rho = baseline["rho"]
    n_rooms = int(baseline["baseline_params"]["n_rooms"])
    
    # Constraint sets spanning low -> high conflict
    sets = [
        ["connected", f"rooms_eq_{n_rooms}"],
        ["connected", "solvable", f"rooms_eq_{n_rooms}"],
        ["connected", "solvable", "two_routes", f"rooms_eq_{n_rooms}"],
        ["connected", "solvable", "one_bottleneck", f"rooms_eq_{n_rooms}"],
        ["connected", "solvable", "two_routes", "one_bottleneck", f"rooms_eq_{n_rooms}"],
        ["connected", "solvable", f"pathlen_eq_{n_rooms-1}", f"rooms_eq_{n_rooms}"],
        ["connected", "solvable", "two_routes", f"pathlen_eq_{n_rooms-1}", f"rooms_eq_{n_rooms}"],
    ]
    
    rows = []
    for cs in sets:
        rho_set = set_conflict_score(rho, cs)
        run_base = f"R{n_rooms}_k{len(cs)}_rho{rho_set:.2f}_" + "_".join(cs[:2])
        
        rows.append({
            "run_id": run_base + "_one_shot",
            "protocol": "one_shot",
            "constraints": cs,
            "rho_set": rho_set,
            "prompt": make_prompt_one_shot(cs, n_rooms),
            "dsl": None,
        })
        
        rows.append({
            "run_id": run_base + "_staged",
            "protocol": "staged",
            "constraints": cs,
            "rho_set": rho_set,
            "prompt": make_prompt_staged(cs, n_rooms),
            "dsl": None,
        })
    
    template = outdir / "bench_template.jsonl"
    with template.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    
    write_json(outdir / "bench_manifest.json", {
        "n_rooms": n_rooms,
        "constraint_sets": sets,
        "n_tasks": len(rows),
        "how_to_use": "Fill the dsl field for each line with JSON output from a model, then eval.",
    })
    
    print(f"[bench] wrote template: {template}")

def cmd_synthesize(args: argparse.Namespace) -> None:
    outdir = Path(args.out)
    ensure_dir(outdir)

    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    n_rooms = int(baseline["baseline_params"]["n_rooms"])

    rows = read_jsonl(Path(args.infile))
    rng = random.Random(args.seed)
    out_rows = []

    started = time.time()
    for r in rows:
        constraints = _coerce_constraints(r.get("constraints", []))
        dsl, attempts, pass_count = synthesize_dsl(
            constraints=constraints,
            n_rooms=n_rooms,
            rng=rng,
            max_attempts=args.max_attempts,
            run_id=str(r.get("run_id", "synth")),
            protocol=str(r.get("protocol", "synth")),
        )
        r["dsl"] = dsl
        r["synth_attempts"] = attempts
        r["synth_pass_count"] = pass_count
        r["synth_passed_all"] = int(pass_count == len(constraints))
        out_rows.append(r)

    out_file = outdir / "bench_filled.jsonl"
    with out_file.open("w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r) + "\n")

    write_json(outdir / "synth_manifest.json", {
        "seed": args.seed,
        "max_attempts": args.max_attempts,
        "n_rows": len(out_rows),
        "runtime_sec": round(time.time() - started, 2),
        "baseline": str(Path(args.baseline)),
        "input": str(Path(args.infile)),
        "output": str(out_file),
    })

    print(f"[synthesize] wrote {out_file} ({len(out_rows)} rows)")

def cmd_eval(args: argparse.Namespace) -> None:
    outdir = Path(args.out)
    ensure_dir(outdir)
    
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    rho = baseline["rho"]
    n_rooms = int(baseline["baseline_params"]["n_rooms"])
    
    in_rows = read_jsonl(Path(args.infile))
    candidates: List[Candidate] = []
    
    for r in in_rows:
        candidates.append(Candidate(
            run_id=str(r.get("run_id", "")),
            protocol=str(r.get("protocol", "one_shot")),
            constraints=list(r.get("constraints", [])),
            dsl=r.get("dsl", None),
        ))
    
    eval_rows: List[Dict[str, object]] = []
    for c in candidates:
        row = eval_candidate(c, n_rooms=n_rooms)
        row["rho_set"] = set_conflict_score(rho, c.constraints)
        eval_rows.append(row)
    
    write_csv(outdir / "eval_rows.csv", eval_rows)
    write_json(outdir / "eval_rows.json", eval_rows)
    
    plot_pass_vs_rho(eval_rows, outdir)
    plot_staging_benefit(eval_rows, outdir)
    plot_phase_diagram(eval_rows, outdir)
    
    print(f"[eval] wrote {len(eval_rows)} rows to {outdir}")
    
    # Summary stats
    by_proto = {}
    for r in eval_rows:
        p = str(r.get("protocol", ""))
        by_proto.setdefault(p, []).append(int(r.get("passed_all", 0)))
    
    print("\nPass rate by protocol:")
    for p, vals in sorted(by_proto.items()):
        print(f"  {p}: {np.mean(vals):.1%} ({sum(vals)}/{len(vals)})")

def cmd_repair(args: argparse.Namespace) -> None:
    """Generate repair prompts for failed candidates."""
    outdir = Path(args.out)
    ensure_dir(outdir)
    
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    n_rooms = int(baseline["baseline_params"]["n_rooms"])
    
    in_rows = read_jsonl(Path(args.infile))
    
    # First evaluate to get failures
    candidates: List[Candidate] = []
    for r in in_rows:
        candidates.append(Candidate(
            run_id=str(r.get("run_id", "")),
            protocol=str(r.get("protocol", "one_shot")),
            constraints=list(r.get("constraints", [])),
            dsl=r.get("dsl", None),
        ))
    
    repair_prompts = []
    for i, c in enumerate(candidates):
        eval_row = eval_candidate(c, n_rooms=n_rooms)
        
        if int(eval_row.get("passed_all", 0)) == 0:
            repair_prompt = generate_repair_prompt(c, eval_row, n_rooms)
            if repair_prompt:
                repair_prompts.append({
                    "run_id": c.run_id + "_repair",
                    "protocol": c.protocol + "+repair",
                    "constraints": c.constraints,
                    "original_dsl": c.dsl,
                    "repair_prompt": repair_prompt,
                    "dsl": None,  # Fill with repaired DSL
                })
    
    out_file = outdir / "repair_prompts.jsonl"
    with out_file.open("w", encoding="utf-8") as f:
        for r in repair_prompts:
            f.write(json.dumps(r) + "\n")
    
    print(f"[repair] generated {len(repair_prompts)} repair prompts -> {out_file}")
    print(f"  Fill 'dsl' field with model's repaired JSON, then run eval again")

# ----------------------------- 
# Main
# ----------------------------- 

def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="IF DSL diagonal-cost validation harness (ICML-ready + repair protocol)."
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    
    p0 = sub.add_parser("baseline", help="Estimate empirical conflict matrix rho.")
    p0.add_argument("--out", required=True)
    p0.add_argument("--samples", type=int, default=3000)
    p0.add_argument("--seed", type=int, default=7)
    p0.add_argument("--n-rooms", dest="n_rooms", type=int, default=6)
    p0.set_defaults(func=cmd_baseline)
    
    p1 = sub.add_parser("bench", help="Create benchmark template JSONL.")
    p1.add_argument("--baseline", required=True)
    p1.add_argument("--out", required=True)
    p1.set_defaults(func=cmd_bench)
    
    p2 = sub.add_parser("eval", help="Evaluate filled JSONL with objective verifier.")
    p2.add_argument("--in", dest="infile", required=True)
    p2.add_argument("--baseline", required=True)
    p2.add_argument("--out", required=True)
    p2.set_defaults(func=cmd_eval)

    p4 = sub.add_parser("synthesize", help="Fill bench template with synthetic DSL outputs.")
    p4.add_argument("--in", dest="infile", required=True)
    p4.add_argument("--baseline", required=True)
    p4.add_argument("--out", required=True)
    p4.add_argument("--seed", type=int, default=7)
    p4.add_argument("--max-attempts", type=int, default=500)
    p4.set_defaults(func=cmd_synthesize)

    p3 = sub.add_parser("repair", help="Generate repair prompts from failures.")
    p3.add_argument("--in", dest="infile", required=True)
    p3.add_argument("--baseline", required=True)
    p3.add_argument("--out", required=True)
    p3.set_defaults(func=cmd_repair)
    
    return p

def main() -> None:
    args = build_argparser().parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
