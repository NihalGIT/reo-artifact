#!/usr/bin/env python3
"""Implementation-property checks with test logic that does not reuse the code under test.

V1  Trace sufficiency (Proposition 2) for instance-level changes. A seeded random mutation generator,
    independent of the corpus operators, edits the snapshot; the touched identifiers are recomputed by
    a separate implementation of Eq. (1). Oracle: the observed assessment on the mutated snapshot.
    Property: if no traced element is touched, the assessment (state and justifications) is identical.
    Checked for the fresh assessment (validate) and for re-assessment with the previous assessment
    (reassess, which adds the premise-removal rule).
V2  Vocabulary changes: random edits of class axioms of a loaded vocabulary extension. Property: every
    requirement whose decision changes is selected by select_for_reassessment.
V3  Identity migration for single renames of every element, against an oracle that substitutes the new
    identifier in the annotation directly (no migration code). Property: identical decisions; every
    requirement whose trace holds the renamed element is selected.

These checks verify properties of the implementation; they are not an evaluation against an external
semantic oracle. Writes results/property_checks.json.   Usage: python scripts/property_checks.py [N] [SEED]
"""
import json, random, sys, time
from copy import deepcopy
from pathlib import Path

import rdflib
from rdflib import RDF, RDFS, Literal, URIRef, Namespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import reo_checker as R  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 20261103
rng = random.Random(SEED)
EXD = ROOT / "examples" / "medical-emergency"
g0 = R.load_graph([EXD / "hospital.ttl", EXD / "hospital-extension.ttl"])
env0 = R.project(g0)
reqs = R.load_requirements(R.load_graph([EXD / "hospital-requirements.ttl"]))
base = {q.rid: R.validate(env0, q) for q in reqs}


def assess(r):
    return (r.state, tuple(sorted(r.justifications)))


def local(t):
    s = str(t)
    return s.split("#")[-1] if "#" in s else s.rsplit("/", 1)[-1]


EXCL_T = {R.REO.Requirement, R.REO.DerivationRecord, R.REO.ValidationActivity}


def env_triples(g):
    excl = {s for t in EXCL_T for s in g.subjects(RDF.type, t)}
    return {(s, p, o) for s, p, o in g if not isinstance(s, rdflib.BNode) and not isinstance(o, rdflib.BNode)
            and s not in excl and p not in R.DERIVATION_PREDICATES}


def delta(ga, gb):  # Eq. (1), re-implemented
    d = set()
    for s, p, o in env_triples(ga) ^ env_triples(gb):
        d.add(local(s))
        if isinstance(o, URIRef) and p != RDF.type:
            d.add(local(o))
    return d


T0 = env_triples(g0)
inds = sorted({s for s, p, o in T0 if isinstance(s, URIRef) and str(s).startswith(str(R.EX))})
preds = sorted({p for s, p, o in T0 if p != RDF.type})
lits = {}
for s, p, o in T0:
    if isinstance(o, Literal):
        lits.setdefault(p, set()).add(o)
lits = {p: sorted(v) for p, v in lits.items()}
classes = sorted({o for s, p, o in T0 if p == RDF.type})
triples = sorted(t for t in T0 if isinstance(t[0], URIRef))


def value(p):
    if p in lits and rng.random() < 0.8:
        v = rng.choice(lits[p])
        return Literal(str(v) + "_x") if rng.random() < 0.15 else v
    return rng.choice(inds)


def mutate(g):
    for _ in range(rng.choice([1, 1, 1, 2, 3])):
        k = rng.choice(["del", "del", "add", "add", "replace", "retype", "delelem"])
        if k == "del":
            g.remove(rng.choice(triples))
        elif k == "add":
            p = rng.choice(preds); g.add((rng.choice(inds), p, value(p)))
        elif k == "replace":
            s, p, o = rng.choice(triples); g.remove((s, p, o))
            g.add((s, p, rng.choice(inds) if isinstance(o, URIRef) else value(p)))
        elif k == "retype":
            g.add((rng.choice(inds), RDF.type, rng.choice(classes)))
        else:
            s = rng.choice(inds)
            for t in list(g.triples((s, None, None))):
                g.remove(t)


out = {"seed": SEED}
t = time.time(); viol = mism = changed = pairs = 0
h_changed = h_viol = h_differs = 0
for i in range(N):
    g1 = deepcopy(g0); mutate(g1); env1 = R.project(g1); d = delta(g0, g1)
    mism += d != R.touched_identifiers(g0, g1)
    for q in reqs:
        pairs += 1; r1 = R.validate(env1, q); c = assess(r1) != assess(base[q.rid]); changed += c
        viol += c and not (base[q.rid].trace & d)
        h = R.reassess(env1, q, previous=base[q.rid]); hc = assess(h) != assess(base[q.rid])
        h_changed += hc; h_viol += hc and not (base[q.rid].trace & d); h_differs += h.state != r1.state
out["V1"] = {"mutants": N, "assessments": pairs, "changed_assessments": changed, "violations": viol,
             "delta_mismatches": mism, "reassess_changed": h_changed, "reassess_violations": h_viol,
             "reassess_state_differs_from_fresh": h_differs, "seconds": round(time.time() - t, 1)}

# V2: vocabulary edits on a loaded extension
EXT = ROOT / "evaluation" / "extension" / "temporal-extension.ttl"
TX = Namespace("http://re-environment-onto.org/ext/temporal#")
gx = deepcopy(g0) + R.load_graph([EXT])
for k, (who, lvl) in enumerate((("Physician", "hard"), ("Pharmacist", "hard"), ("I001_PhysicianToEHR", "soft"))):
    c = R.EX[f"C96{k}_Temporal"]
    gx.add((c, RDF.type, TX.TemporalConstraint)); gx.add((c, R.REO.enforcementLevel, Literal(lvl)))
    gx.add((c, R.REO.appliesTo, R.EX[who]))
envx = R.project(gx)
bx = {q.rid: R.validate(envx, q) for q in reqs}
recs = {rid: r.trace for rid, r in bx.items()}
roots = [R.REO.Constraint, R.REO.Goal, R.REO.Actor, R.REO.Context, R.REO.Uncertainty]
v2 = {"edits": 0, "decision_changes": 0, "missed": 0, "reason_vocabulary": 0}
for _ in range(max(50, N // 25)):
    gy = deepcopy(gx)
    if rng.random() < 0.5:
        gy.remove((TX.TemporalConstraint, RDFS.subClassOf, R.REO.Constraint))
    gy.add((TX.TemporalConstraint, RDFS.subClassOf, rng.choice(roots)))
    envy = R.project(gy); sel, why = R.select_for_reassessment(gx, gy, recs)
    v2["edits"] += 1; v2["reason_vocabulary"] += why == "vocabulary"
    for q in reqs:
        if R.validate(envy, q).state != bx[q.rid].state:
            v2["decision_changes"] += 1; v2["missed"] += q.rid not in sel
out["V2"] = v2


def rename(g, old, new):
    for s, p, o in list(g.triples((R.EX[old], None, None))):
        g.remove((s, p, o)); g.add((R.EX[new], p, o))
    for s, p, o in list(g.triples((None, None, R.EX[old]))):
        g.remove((s, p, o)); g.add((s, p, R.EX[new]))
    g.add((R.EX[old], R.REO.supersededBy, R.EX[new]))


def substitute(q, old, new):
    q2 = deepcopy(q)
    for a in ("seed_goal", "context", "actor", "authority_of", "interaction", "interaction_source"):
        if getattr(q2, a) == old:
            setattr(q2, a, new)
    for a in ("actions", "properties", "hard", "soft", "concepts"):
        setattr(q2, a, sorted({new if x == old else x for x in getattr(q2, a)}))
    return q2


v3 = {"elements": 0, "pairs": 0, "mismatches": 0, "unselected": 0}
for e in sorted(env0.all_ids()):
    g1 = deepcopy(g0); rename(g1, e, e + "_v2"); env1 = R.project(g1); m = R.supersession_map(g1)
    d = R.touched_identifiers(g0, g1); v3["elements"] += 1
    for q in reqs:
        v3["pairs"] += 1
        v3["mismatches"] += R.reassess(env1, q, m).state != R.validate(env1, substitute(q, e, e + "_v2")).state
        v3["unselected"] += (e in base[q.rid].trace) and not (base[q.rid].trace & d)
out["V3"] = v3
(ROOT / "results").mkdir(exist_ok=True)
(ROOT / "results" / "property_checks.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print(json.dumps(out, indent=1))
ok = out["V1"]["violations"] == 0 and out["V1"]["delta_mismatches"] == 0 and h_viol == 0 and v2["missed"] == 0 \
    and v3["mismatches"] == 0 and v3["unselected"] == 0
sys.exit(0 if ok else 1)
