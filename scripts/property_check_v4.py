#!/usr/bin/env python3
"""V4: trace sufficiency (Proposition 2) on random *base* snapshots.

V1 (property_checks.py) always starts from the running example, in which every element is typed. Some changes
cannot arise from that base, for instance an element that already links into the evidence through appliesTo, blocks
or affects, has no view type, and later receives one. V4 therefore first derives a random base snapshot S1 from the
example (removing the type of an element, adding an untyped element with a link, or a V1-style edit), assesses the
ten requirements on S1, then applies a second random edit (giving a type to an untyped element, adding a link, or a
V1-style edit) to obtain S2.

Property: if no element of the S1 trace is touched by the S1 -> S2 change (touched identifiers, Eq. (2) of the
article, re-implemented here), the assessment (state and justifications) on S2 equals the one on S1.
Writes results/property_checks_v4.json.

    python scripts/property_check_v4.py [--n N] [--seed SEED] [--out PATH]
"""
import argparse, importlib.util, json, random, sys, time
from copy import deepcopy
from pathlib import Path

import rdflib
from rdflib import RDF, Literal, URIRef

ROOT = Path(__file__).resolve().parents[1]
EXD = ROOT / "examples" / "medical-emergency"


def load_checker(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod; spec.loader.exec_module(mod)
    return mod


def local(t):
    s = str(t)
    return s.split("#")[-1] if "#" in s else s.rsplit("/", 1)[-1]


def snapshots(n: int, seed: int):
    """The seeded sequence of (S1, S2) pairs; it does not use the checker under test."""
    R = load_checker(ROOT / "reo_checker.py", "reo_checker_gen")   # only for file loading and namespaces
    rng = random.Random(seed)
    g0 = R.load_graph([EXD / "hospital.ttl", EXD / "hospital-extension.ttl"])
    REO, EX = R.REO, R.EX
    T0 = sorted(t for t in g0 if isinstance(t[0], URIRef))
    inds = sorted({s for s, _, _ in T0 if str(s).startswith(str(EX))})
    preds = sorted({p for _, p, _ in T0 if p != RDF.type})
    classes = sorted({o for _, p, o in T0 if p == RDF.type})
    lits = {}
    for s, p, o in T0:
        if isinstance(o, Literal):
            lits.setdefault(p, set()).add(o)
    lits = {p: sorted(v) for p, v in lits.items()}
    links = [REO.appliesTo, REO.blocks, REO.affects]
    attrs = [(REO.enforcementLevel, Literal("hard")), (REO.status, Literal("unvalidated")),
             (REO.impactLevel, Literal("high")), (REO.validatorKey, Literal("authority-equals"))]

    def value(p):
        if p in lits and rng.random() < 0.8:
            return rng.choice(lits[p])
        return rng.choice(inds)

    def v1_edit(g):
        k = rng.choice(["del", "add", "replace", "retype"])
        if k == "del":
            g.remove(rng.choice(T0))
        elif k == "add":
            p = rng.choice(preds); g.add((rng.choice(inds), p, value(p)))
        elif k == "replace":
            s, p, o = rng.choice(T0); g.remove((s, p, o)); g.add((s, p, rng.choice(inds) if isinstance(o, URIRef) else value(p)))
        else:
            g.add((rng.choice(inds), RDF.type, rng.choice(classes)))

    def untyped(g):
        subj = {s for s, _, _ in g if isinstance(s, URIRef) and str(s).startswith(str(EX))}
        return sorted(s for s in subj if (s, RDF.type, None) not in g)

    for i in range(n):
        g1 = deepcopy(g0)
        k1 = rng.choice(["untype", "untype", "orphan", "orphan", "v1"])
        if k1 == "untype":
            s = rng.choice(inds)
            for t in list(g1.triples((s, RDF.type, None))):
                g1.remove(t)
        elif k1 == "orphan":
            x = EX[f"X4_{i}"]
            g1.add((x, rng.choice(links), rng.choice(inds)))
            for a in rng.sample(attrs, rng.randint(0, len(attrs))):
                g1.add((x, *a))
        else:
            v1_edit(g1)
        g2 = deepcopy(g1)
        k2 = rng.choice(["type", "type", "link", "v1"])
        cand = untyped(g1)
        if k2 == "type" and cand:
            g2.add((rng.choice(cand), RDF.type, rng.choice(classes)))
        elif k2 == "link":
            src = rng.choice(cand) if cand and rng.random() < 0.5 else rng.choice(inds)
            g2.add((src, rng.choice(links), rng.choice(inds)))
        else:
            v1_edit(g2)
        yield k1, k2, g1, g2


def delta(ga, gb, excl_pred):
    """Eq. (1), re-implemented: subjects and IRI objects (other than rdf:type classes) of changed triples."""
    def env(g):
        return {(s, p, o) for s, p, o in g if not isinstance(s, rdflib.BNode) and not isinstance(o, rdflib.BNode)
                and p not in excl_pred}
    d = set()
    for s, p, o in env(ga) ^ env(gb):
        d.add(local(s))
        if isinstance(o, URIRef) and p != RDF.type:
            d.add(local(o))
    return d


def run(R, pairs, label):
    reqs = R.load_requirements(R.load_graph([EXD / "hospital-requirements.ttl"]))
    t = time.time()
    out = {"checker": label, "policy": R.POLICY_VERSION, "pairs": 0, "assessments": 0, "changed": 0,
           "violations": 0, "delta_mismatches": 0, "by_kind": {}, "examples": []}
    for k1, k2, g1, g2 in pairs:
        env1, env2 = R.project(g1), R.project(g2)
        d = delta(g1, g2, R.DERIVATION_PREDICATES)
        out["pairs"] += 1; out["delta_mismatches"] += d != R.touched_identifiers(g1, g2)
        kind = f"{k1}->{k2}"
        bk = out["by_kind"].setdefault(kind, {"pairs": 0, "changed": 0, "violations": 0})
        bk["pairs"] += 1
        for q in reqs:
            r1, r2 = R.validate(env1, q), R.validate(env2, q)
            out["assessments"] += 1
            c = (r1.state, sorted(r1.justifications)) != (r2.state, sorted(r2.justifications))
            out["changed"] += c; bk["changed"] += c
            if c and not (set(r1.trace) & d):
                out["violations"] += 1; bk["violations"] += 1
                if len(out["examples"]) < 5:
                    out["examples"].append({"kind": kind, "requirement": q.rid, "delta": sorted(d),
                                            "before": r1.state, "after": r2.state})
    out["seconds"] = round(time.time() - t, 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=20261105)
    ap.add_argument("--out", default=str(ROOT / "results" / "property_checks_v4.json"))
    a = ap.parse_args()
    pairs = list(snapshots(a.n, a.seed))
    res = {"seed": a.seed, "n": a.n,
           "current": run(load_checker(ROOT / "reo_checker.py", "reo_checker"), pairs, "reo_checker.py")}
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    for k in ("current",):
        if k in res:
            x = res[k]
            print(f"{k:8s} {x['policy']}: {x['pairs']} pairs, {x['assessments']} assessments, {x['changed']} changed, "
                  f"{x['violations']} violations, {x['delta_mismatches']} delta mismatches ({x['seconds']}s)")
    return 0 if res["current"]["violations"] == 0 and res["current"]["delta_mismatches"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
