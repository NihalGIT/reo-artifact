#!/usr/bin/env python3
"""Corner cases of REO v1.0.0 (policy ecre-policy-1.0.0).

Each case states the behaviour that the contract's semantics require (expected), observes the
checker, and reports pass/fail. Expectations are written from the semantics stated in the article
(Sections V and VI), not copied from the checker's output. Kinds: positive and negative (each targets
one rule of the contract), boundary, regression, property.

    python scripts/check_corrections.py                      # results/corrections_checks.json
    python scripts/check_corrections.py --checker PATH.py --out OUT.json
                                                             # same cases against another checker module
Exit code 0 only if every case passes.
"""
from __future__ import annotations

import argparse, importlib.util, itertools, json, os, subprocess, sys
from copy import deepcopy
from pathlib import Path

import rdflib
from rdflib import RDF, RDFS, OWL, BNode, Literal, Namespace

ROOT = Path(__file__).resolve().parents[1]
EXD = ROOT / "examples" / "medical-emergency"
EXT = ROOT / "evaluation" / "extension" / "temporal-extension.ttl"
TX = Namespace("http://re-environment-onto.org/ext/temporal#")


def _without(g, s):
    g2 = deepcopy(g)
    g2.remove((s, None, None))
    return g2


def load_checker(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod; spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checker", dest="old", help="path of another reo_checker.py module to run the same cases against")
    ap.add_argument("--out", default=str(ROOT / "results" / "corrections_checks.json"))
    args = ap.parse_args()
    R = load_checker(Path(args.old), "reo_checker_under_test") if args.old else load_checker(ROOT / "reo_checker.py", "reo_checker")
    if args.old:  # the old module reads its ontology from its own folder; use this release's files for data
        pass
    E = R.EX
    g0 = R.load_graph([EXD / "hospital.ttl", EXD / "hospital-extension.ttl"])
    env0 = R.project(g0)
    reqs = {q.rid: q for q in R.load_requirements(R.load_graph([EXD / "hospital-requirements.ttl"]))}
    base = {rid: R.validate(env0, q) for rid, q in reqs.items()}
    rows = []

    def case(cid, corr, kind, expected, observed, why):
        rows.append({"id": cid, "correction": corr, "kind": kind, "expected": expected, "observed": observed,
                     "pass": expected == observed, "rationale": why})

    def req(**kw):
        d = dict(rid="T", text="test", seed_goal="G005", context="X001_EmergencyDept"); d.update(kw)
        return R.Requirement(**d)

    def has(r, text):
        return any(text in j for j in r.justifications)

    reassess = getattr(R, "reassess", None)

    def reassess_(env, q, mapping=None, previous=None):
        if reassess is not None:
            return reassess(env, q, mapping, previous)
        return R.rederive(env, q, mapping or {})          # 1.1.x: no history

    # ------------------------------------------------------------------ P1: O2 without a responsible actor
    r = R.validate(env0, req(actions=["Action_Diagnose"]))
    case("P1-a", "P1", "negative", "pending_review", r.state,
         "O2 relates an actor to an action; with no responsible actor the capability cannot be decided (hard, unchecked)")
    r = R.validate(env0, req(actions=["Action_Diagnose"], concepts=["D001_Medication"]))
    case("P1-b", "P1", "boundary", "pending_review", r.state,
         "the concept check passes, but the capability of the annotated action is still undecided; "
         "a general non-vacuity guard would accept this candidate")
    r = R.validate(env0, req(actor="Physician", actions=["Action_Diagnose"]))
    case("P1-c", "P1", "regression", "accepted", r.state, "actor and capability given: O1 and O2 pass")
    r = R.validate(env0, req(concepts=["D001_Medication"]))
    case("P1-d", "P1", "regression", "accepted", r.state, "a referenced concept that exists is a discharged obligation")
    r = R.validate(env0, req())
    case("P1-e", "P1", "regression", "pending_review", r.state, "no referenced element: nothing to check")

    # property: every accepted / accepted-with-reservations / conditional decision rests on a passed O1-O6 check
    fields = {"actor": "Physician", "actions": ["Action_Diagnose"], "authority_of": "Physician",
              "concepts": ["D001_Medication"], "properties": ["Prop_Dosage"], "interaction": "I001_PhysicianToEHR",
              "interaction_source": "Physician", "requires_protocol": True, "hard": ["C002_FDA"],
              "soft": ["C003_ResponseTime"]}
    names, bad, n = list(fields), [], 0
    o16 = ("O1", "O2", "O3", "O4", "O5", "O6")
    for k in range(len(names) + 1):
        for combo in itertools.combinations(names, k):
            for seed, ctx in (("G002", "X001_EmergencyDept"), ("G005", "X001_EmergencyDept"), ("G003", "X002_Outpatient")):
                q = req(seed_goal=seed, context=ctx, **{f: deepcopy(fields[f]) for f in combo})
                rr = R.validate(env0, q); n += 1
                passes = [m for lvl, o, m in rr.obligations if o == R.PASS and "blocking" not in m and "affecting" not in m]
                if rr.state in (R.ACCEPTED, R.ACCEPTED_WITH_RESERVATIONS, R.CONDITIONAL) and not passes:
                    bad.append("+".join(combo) + "@" + seed)
    case("P1-f", "P1", "property", 0, len(bad),
         f"over {n} annotation shapes, no acceptance-type or conditional decision without a passed O1-O6 check "
         f"(examples of violations: {sorted(set(bad))[:3]})")

    # ------------------------------------------------------------------ P6: guard = refs(q)
    r = R.validate(env0, req(authority_of="Physician"))
    case("P6-a", "P6", "positive", "accepted", r.state,
         "the authority holder is a reference; its O3 check passes")
    r = R.validate(env0, req(interaction_source="Physician"))
    case("P6-b", "P6", "negative", "pending_review+no-interaction",
         r.state + ("+no-interaction" if has(r, "without a used interaction") else ""),
         "an expected source is a reference, but without a used interaction it cannot be checked")
    r = R.validate(env0, req(concepts=["D001_Medication"], requires_protocol=True))
    case("P6-c", "P6", "negative", "pending_review", r.state,
         "a required protocol without a used interaction cannot be checked")

    # ------------------------------------------------------------------ P7: O6 justification
    r = R.validate(env0, req(seed_goal="G002", interaction="I001_PhysicianToEHR"))
    o6 = [j for j in r.justifications if "I001_PhysicianToEHR" in j and j.startswith("OK")]
    claims = any(w in " ".join(o6) for w in ("endpoint", "direction", "data", "protocol check"))
    honest = any("requests no endpoint" in j for j in o6)
    case("P7-a", "P7", "negative", "no-unperformed-claims", "no-unperformed-claims" if (o6 and honest) else
         ("claims-unperformed-checks" if claims else "missing"), "only existence was checked")
    r = R.validate(env0, reqs["R001"])
    o6 = " ".join(j for j in r.justifications if "I001_PhysicianToEHR" in j and j.startswith("OK"))
    case("P7-b", "P7", "regression", True, all(w in o6 for w in ("endpoint", "direction", "data", "protocol")),
         "R001 requests all four sub-checks")

    # ------------------------------------------------------------------ P2: removal of a premise
    U = E["U001_DrugDBFrequency"]
    q1 = reqs["R001"]; prev = base["R001"]

    def variant(fn):
        g = deepcopy(g0); fn(g); return g, R.project(g)

    g1, env1 = variant(lambda g: g.remove((U, None, None)))
    r = reassess_(env1, q1, None, prev)
    case("P2-a", "P2", "negative", "pending_review", r.state,
         "the open premise U001 disappeared without a recorded resolution; removal is not validation")
    case("P2-a'", "P2", "boundary", "accepted_with_reservations", R.validate(env1, q1).state,
         "a fresh assessment of the new snapshot alone (no history) does not see the premise")
    g1, env1 = variant(lambda g: g.remove((U, RDF.type, None)))
    case("P2-b", "P2", "negative", "pending_review", reassess_(env1, q1, None, prev).state,
         "removing only the type of U001 also removes the premise without resolution")
    gv, envv = variant(lambda g: (g.remove((U, R.REO.status, None)), g.add((U, R.REO.status, Literal("validated")))))
    prev_valid = R.validate(envv, q1)
    gvd = deepcopy(gv); gvd.remove((U, None, None))
    case("P2-c", "P2", "boundary", "accepted_with_reservations", reassess_(R.project(gvd), q1, None, prev_valid).state,
         "the premise was established (validated) before its removal: nothing unresolved disappears")

    def ren_u(g):
        for s, p, o in list(g.triples((U, None, None))):
            g.remove((s, p, o)); g.add((E["U001b"], p, o))
        g.add((U, R.REO.supersededBy, E["U001b"]))
    g1, env1 = variant(ren_u)
    case("P2-d", "P2", "boundary", "conditional", reassess_(env1, q1, R.supersession_map(g1), prev).state,
         "renamed premise: the successor exists and is still open")
    g1, env1 = variant(lambda g: (g.remove((U, R.REO.blocks, None)), g.remove((U, R.REO.affects, None))))
    case("P2-e", "P2", "boundary", "accepted_with_reservations", reassess_(env1, q1, None, prev).state,
         "the premise still exists; the model now states explicitly that it bears on nothing the requirement reads")
    gr, envr = variant(lambda g: (g.remove((U, R.REO.status, None)), g.add((U, R.REO.status, Literal("rejected")))))
    prev_ref = R.validate(envr, q1)
    grd = deepcopy(gr); grd.remove((U, None, None))
    case("P2-f", "P2", "negative", "pending_review", reassess_(R.project(grd), q1, None, prev_ref).state,
         f"a refuted premise ({prev_ref.state}) that disappears must not lead to acceptance")
    case("P2-g", "P2", "regression", "conditional", reassess_(env0, q1, None, prev).state, "no change")
    # A soft premise (unvalidated, low impact) that affects the evidence gives a reservation; its removal
    # without resolution keeps the reservation at the same level and does not send the requirement to review.
    U9 = E["U960_LowImpact"]
    qs = req(actor="Physician", actions=["Action_Diagnose"])

    def add_u9(g):
        g.add((U9, RDF.type, R.REO.UnknownUncertainty)); g.add((U9, R.REO.status, Literal("unvalidated")))
        g.add((U9, R.REO.impactLevel, Literal("low"))); g.add((U9, R.REO.affects, E["Physician"]))
    gs, envs = variant(add_u9)
    prev_soft = R.validate(envs, qs)
    case("P2-h", "P2", "boundary", "accepted_with_reservations->accepted_with_reservations",
         f"{prev_soft.state}->{reassess_(env0, qs, None, prev_soft).state}",
         "a low-impact open premise only causes a reservation; its removal keeps that level (no review)")
    gsr = deepcopy(gs); gsr.remove((E["Physician"], R.REO.hasCapability, E["Action_Diagnose"]))
    case("P2-i", "P2", "boundary", "rejected", reassess_(R.project(_without(gsr, U9)), qs, None, prev_soft).state,
         "a failed hard check still rejects when a soft premise is also removed")

    # ------------------------------------------------------------------ P3: vocabulary changes
    gx = deepcopy(g0) + R.load_graph([EXT])
    C9 = E["C960_ShiftRule"]
    gx.add((C9, RDF.type, TX.TemporalConstraint)); gx.add((C9, R.REO.enforcementLevel, Literal("hard")))
    gx.add((C9, R.REO.appliesTo, E["Physician"]))
    envx = R.project(gx)
    recs = {rid: R.validate(envx, q).trace for rid, q in reqs.items()}
    gy = deepcopy(gx); gy.remove((TX.TemporalConstraint, RDFS.subClassOf, R.REO.Constraint))
    changed = {rid for rid, q in reqs.items() if R.validate(envx, q).state != R.validate(R.project(gy), q).state}
    sel_fn = getattr(R, "select_for_reassessment", None)
    sel = sel_fn(gx, gy, recs)[0] if sel_fn else R.direct_reevaluation(R.touched_identifiers(gx, gy), recs)
    case("P3-a", "P3", "negative", True, bool(changed) and changed <= sel,
         f"a subclass axiom of the snapshot changes decisions {sorted(changed)}; every changed decision must be selected")
    gz = deepcopy(g0); gz.remove((E["Physician"], R.REO.authorityLevel, None))
    gz.add((E["Physician"], R.REO.authorityLevel, Literal("resident_physician")))
    trace0 = {rid: b.trace for rid, b in base.items()}
    sel_z = sel_fn(g0, gz, trace0) if sel_fn else (R.direct_reevaluation(R.touched_identifiers(g0, gz), trace0), "trace")
    case("P3-b", "P3", "regression", ("trace", sorted(R.direct_reevaluation(R.touched_identifiers(g0, gz), trace0))),
         (sel_z[1], sorted(sel_z[0])), "an instance-level change keeps the trace-based selection")
    fp = getattr(R, "vocabulary_fingerprint", None)
    gb = deepcopy(g0); rs = BNode()
    gb.add((TX.NightRule, RDFS.subClassOf, rs)); gb.add((rs, RDF.type, OWL.Restriction))
    gb.add((rs, OWL.onProperty, R.REO.appliesTo))
    case("P3-c", "P3", "boundary", (True, False),
         (fp(g0) == fp(gz), fp(g0) == fp(gb)) if fp else ("no fingerprint", None),
         "instance changes keep the vocabulary fingerprint; a new restriction (blank node) changes it")

    # ------------------------------------------------------------------ P4: identifier collisions
    probe = ("import sys,json; sys.path.insert(0, sys.argv[2]); import importlib.util as u;"
             "sp=u.spec_from_file_location('m', sys.argv[1]); m=u.module_from_spec(sp); sys.modules['m']=m; sp.loader.exec_module(m);"
             "from rdflib import RDF, Literal, Namespace; O=Namespace('http://other.example.org#');"
             "ex=sys.argv[2]+'/examples/medical-emergency/';"
             "g=m.load_graph([ex+'hospital.ttl', ex+'hospital-extension.ttl']);"
             "g.add((O[sys.argv[3]], RDF.type, m.REO.HumanActor if sys.argv[3]=='Physician' else m.REO.OperationalGoal));"
             "g.add((O[sys.argv[3]], m.REO.authorityLevel, Literal('none')));"
             "q=[q for q in m.load_requirements(m.load_graph([ex+'hospital-requirements.ttl'])) if q.rid=='R001'][0];"
             "r=m.validate(m.project(g), q); print(json.dumps([r.state, any('several IRIs' in j for j in r.justifications)]))")
    checker = args.old or str(ROOT / "reo_checker.py")
    outs = set()
    for seed in range(8):
        env_s = dict(os.environ, PYTHONHASHSEED=str(seed))
        out = subprocess.run([sys.executable, "-c", probe, checker, str(ROOT), "Physician"], capture_output=True,
                             text=True, env=env_s, cwd=str(ROOT))
        outs.add(out.stdout.strip() or out.stderr.strip()[-120:])
    case("P4-a", "P4", "negative", ['["pending_review", true]'], sorted(outs),
         "two IRIs share the local name Physician: the outcome must not depend on the hash seed, and no check "
         "on the ambiguous identifier is decided")
    case("P4-b", "P4", "regression", True, all(b.state == R.validate(R.project(deepcopy(g0)), q).state
                                               for q, b in ((reqs[k], base[k]) for k in reqs)),
         "a model without collisions is unaffected")
    out = subprocess.run([sys.executable, "-c", probe, checker, str(ROOT), "G004"], capture_output=True, text=True,
                         cwd=str(ROOT))
    case("P4-c", "P4", "boundary", '["conditional", false]', out.stdout.strip(),
         "a collision on an element that R001 does not read leaves R001 unchanged")

    # ------------------------------------------------------------------ P5: record identity
    qa = reqs["R001"]; qb = deepcopy(qa); qb.actions = sorted(qb.actions + ["Action_Dispense"])
    ra, rb = R.validate(env0, qa), R.validate(env0, qb)
    rg = R.record_graph([ra, rb])
    rec_ids = sorted(set(rg.subjects(RDF.type, R.REO.DerivationRecord)))
    decisions = sorted(len(set(rg.objects(x, R.REO.hasDecision))) for x in rec_ids)
    case("P5-a", "P5", "negative", (2, [1, 1]), (len(rec_ids), decisions),
         "two annotations of one candidate on one snapshot are two records with one decision each")
    rg2 = R.record_graph([ra, R.validate(env0, qa)])
    case("P5-b", "P5", "regression", 1, len(set(rg2.subjects(RDF.type, R.REO.DerivationRecord))),
         "the same candidate assessed twice on the same snapshot is the same record")
    qt = deepcopy(qa); qt.text = "The Pharmacist may ignore contraindications."
    rt = R.validate(env0, qt)
    rg3 = R.record_graph([ra, rt])
    case("P5-c", "P5", "boundary", (True, 2), (rt.state == ra.state, len(set(rg3.subjects(RDF.type, R.REO.DerivationRecord)))),
         "a changed statement with an unchanged annotation keeps the decision (T18) but is a distinct record")
    try:
        from pyshacl import validate as shacl
        dg = R.load_graph(R.ONTOLOGY_FILES + [ROOT / "ontology" / "reo-shapes.ttl"])
        shapes = R.load_graph([ROOT / "ontology" / "reo-derivation-shapes.ttl"])
        data = deepcopy(g0) + R.load_graph([EXD / "hospital-requirements.ttl"]) + R.record_graph(list(base.values()))
        conforms = shacl(data, shacl_graph=shapes, ont_graph=R.load_graph(R.ONTOLOGY_FILES), inference="rdfs")[0]
    except Exception as ex:  # reported, not hidden
        conforms = f"error: {ex}"
    case("P5-d", "P5", "property", True, conforms,
         "records of the ten requirements conform to the derivation shapes, including the non-vacuity constraint")

    # ------------------------------------------------------------------ P8: supersession chains
    def chain(g):
        old, new = "D001_Medication", "D001c_Med"
        for s, p, o in list(g.triples((E[old], None, None))):
            g.remove((s, p, o)); g.add((E[new], p, o))
        for s, p, o in list(g.triples((None, None, E[old]))):
            g.remove((s, p, o)); g.add((s, p, E[new]))
        g.add((E[old], R.REO.supersededBy, E["D001b_Med"])); g.add((E["D001b_Med"], R.REO.supersededBy, E[new]))
    g1, env1 = variant(chain)
    case("P8-a", "P8", "negative", "pending_review", reassess_(env1, q1, R.supersession_map(g1)).state,
         "only one supersession step is migrated; a successor that is itself superseded goes to review, "
         "not to a rejection for an undefined element")

    def ren_d(g):
        for s, p, o in list(g.triples((E["D001_Medication"], None, None))):
            g.remove((s, p, o)); g.add((E["D001_Med2"], p, o))
        for s, p, o in list(g.triples((None, None, E["D001_Medication"]))):
            g.remove((s, p, o)); g.add((s, p, E["D001_Med2"]))
        g.add((E["D001_Medication"], R.REO.supersededBy, E["D001_Med2"]))
    g1, env1 = variant(ren_d)
    case("P8-b", "P8", "regression", "conditional", reassess_(env1, q1, R.supersession_map(g1)).state, "one rename")

    # ------------------------------------------------------------------ P9: supersedes
    gv1, envv1 = variant(lambda g: (g.remove((U, R.REO.status, None)), g.add((U, R.REO.status, Literal("validated")))))
    rnew = reassess_(envv1, q1, None, prev)
    try:
        rgp = R.record_graph([rnew], previous={"R001": prev})
        links = list(rgp.objects(None, R.REO.supersedes))
        ok = len(links) == 1 and str(links[0]) == str(R.record_iri(prev))
    except TypeError:
        ok = False
    case("P9-a", "P9", "positive", True, ok, "a record produced by re-assessment points to the record it replaces")
    case("P9-b", "P9", "regression", 0, len(list(R.record_graph([prev]).objects(None, R.REO.supersedes))),
         "a first assessment supersedes nothing")

    # ================================================================== read set of incoming links
    # ------------------------------------------------------------------ P10: read set of backward links
    X = E["C990_Untyped"]
    gb10 = deepcopy(g0); gb10.add((X, R.REO.appliesTo, E["Physician"])); gb10.add((X, R.REO.enforcementLevel, Literal("hard")))
    rb10 = R.validate(R.project(gb10), q1)
    gm10 = deepcopy(gb10); gm10.add((X, RDF.type, R.REO.RegulatoryConstraint))
    rm10 = R.validate(R.project(gm10), q1)
    sel10 = q1.rid in R.direct_reevaluation(R.touched_identifiers(gb10, gm10), {q1.rid: rb10.trace})
    case("P10-a", "P10", "negative", (True, True), (rb10.state != rm10.state, sel10),
         "an untyped element with an appliesTo link to the Physician becomes a constraint: the decision of R001 "
         "changes, so R001 must be selected (Proposition 2); the element's type was read, so it is in the trace")
    U9b = E["U991_Untyped"]
    qd = req(actor="Physician", actions=["Action_Diagnose"])
    gb11 = deepcopy(g0)
    for p_, o_ in ((R.REO.affects, E["Physician"]), (R.REO.status, Literal("unvalidated")), (R.REO.impactLevel, Literal("high"))):
        gb11.add((U9b, p_, o_))
    rb11 = R.validate(R.project(gb11), qd)
    gm11 = deepcopy(gb11); gm11.add((U9b, RDF.type, R.REO.UnknownUncertainty))
    rm11 = R.validate(R.project(gm11), qd)
    sel11 = "T" in R.direct_reevaluation(R.touched_identifiers(gb11, gm11), {"T": rb11.trace})
    case("P10-b", "P10", "negative", (True, True), (rb11.state != rm11.state, sel11),
         "an untyped element with an affects link becomes an uncertainty: the changed decision must be selected")
    case("P10-c", "P10", "regression", [15, 12, 9, 9, 12, 9, 10, 13, 10, 10],
         [len(base[k].trace) for k in sorted(base)],
         "the example has no such links, so the ten traces keep their sizes (requirements table of the article)")

    # Shapes of the checker under test (a checker kept in another folder may carry its own shapes).
    shapes_dir = Path(checker).resolve().parent / "ontology"
    if not (shapes_dir / "reo-derivation-shapes.ttl").exists():
        shapes_dir = ROOT / "ontology"
    REQNS = Namespace("http://hospital.example.org/requirements#")
    OX = Namespace("http://other.example.org#")
    rq_g = R.load_graph([EXD / "hospital-requirements.ttl"])
    onto_g = R.load_graph(R.ONTOLOGY_FILES)
    sys.path.insert(0, str(ROOT / "scripts"))
    import sparql_oracle as O

    def annotation(src="R001", new=None, add=(), drop=()):
        """A graph with a copy of the annotation of `src` (subject renamed to `new`): predicates in `drop` are
        left out and the (predicate, object) pairs in `add` are added."""
        ga = rdflib.Graph()
        s_new = rdflib.URIRef(new) if new else REQNS[src]
        for _, p_, o_ in rq_g.triples((REQNS[src], None, None)):
            if p_ not in drop:
                ga.add((s_new, p_, o_))
        for p_, o_ in add:
            ga.add((s_new, p_, o_))
        return ga

    def decide1(ga, env=env0):
        try:
            return R.validate(env, R.load_requirements(ga)[0]).state
        except Exception as ex:  # reported, not hidden
            return f"error: {type(ex).__name__}"

    def oracle1(ga, genv=g0):
        try:
            return next(iter(O.decide(onto_g + genv + ga).values()))
        except Exception as ex:
            return f"error: {type(ex).__name__}"

    def iris_in(g):
        return {str(t) for t in g.all_nodes() if isinstance(t, rdflib.URIRef)}

    # ------------------------------------------------------------------ P11: records name the IRIs
    OQ = "http://other.example.org/requirements#Q1"
    try:
        ro = R.validate(env0, R.load_requirements(annotation(new=OQ))[0])
        rgo = R.record_graph([ro])
        obs = ({str(o) for o in rgo.objects(None, R.REO.assesses)} == {OQ},
               {str(o) for o in rgo.objects(None, R.REO.consulted)} <= iris_in(g0) | iris_in(rq_g))
    except Exception as ex:
        obs = f"error: {type(ex).__name__}"
    case("P11-a", "P11", "negative", (True, True), obs,
         "a requirement outside the example's requirement namespace: its record must assess that requirement, "
         "not an IRI built from the identifier in the hospital namespace")

    def renamespace(g, old, new):
        h = rdflib.Graph()
        f = (lambda t: rdflib.URIRef(new + str(t)[len(old):])
             if isinstance(t, rdflib.URIRef) and str(t).startswith(old) else t)
        for t in g:
            h.add(tuple(f(x) for x in t))
        return h
    CL = "http://clinic.example.org#"
    gc, rqc = renamespace(g0, str(E), CL), renamespace(rq_g, str(E), CL)
    try:
        rc = R.validate(R.project(gc), [q for q in R.load_requirements(rqc) if q.rid == "R001"][0])
        cons = {str(o) for o in R.record_graph([rc]).objects(None, R.REO.consulted)}
        obs = (bool(cons) and cons <= iris_in(gc), rc.state)
    except Exception as ex:
        obs = f"error: {type(ex).__name__}"
    case("P11-b", "P11", "negative", (True, "conditional"), obs,
         "the same model in another namespace gives the same decision; the record must name the consulted "
         "elements by their IRIs in that model, not by IRIs of the hospital namespace")
    rgx = R.record_graph(list(base.values()))
    case("P11-c", "P11", "regression", (True, True),
         ({str(o) for o in rgx.objects(None, R.REO.assesses)} == {str(REQNS[k]) for k in base},
          {str(o) for o in rgx.objects(None, R.REO.consulted)} <= iris_in(g0) | iris_in(rq_g)),
         "in the running example the records assess req:R001-R010 and consult elements of the model, as before")

    # ------------------------------------------------------------------ P12: duplicate requirement identifiers
    gdup = rq_g + annotation(src="R002", new=str(REQNS["R002bis"]), drop={R.REO.requirementId},
                             add=[(R.REO.requirementId, Literal("R001"))])
    try:
        obs = f"loaded {len(R.load_requirements(gdup))} requirements silently"
    except ValueError:
        obs = "refused"
    case("P12-a", "P12", "negative", "refused", obs,
         "two requirement resources share the identifier R001; records and re-assessment are keyed by the "
         "identifier, so one would silently stand for the other")
    case("P12-b", "P12", "regression", 10, len(R.load_requirements(rq_g)), "the ten example identifiers are distinct")
    try:
        O.decide(onto_g + g0 + gdup); obs = "decided silently"
    except ValueError:
        obs = "refused"
    case("P12-c", "P12", "property", "refused", obs, "the SPARQL encoding refuses the duplicate as well")

    # ------------------------------------------------------------------ P13: ill-formed annotations
    ill = {
        "P13-a": (annotation(add=[(R.REO.hasSeedGoal, E["G005"])]), "two seed goals (G002, G005)"),
        "P13-b": (annotation(add=[(R.REO.hasResponsibleActor, E["Pharmacist"])]),
                  "two responsible actors (Physician, Pharmacist)"),
        "P13-c": (annotation(drop={R.REO.hasResponsibleActor}, add=[(R.REO.hasResponsibleActor, Literal("Physician"))]),
                  "the responsible actor is the literal \"Physician\", not an element"),
        "P13-d": (annotation(drop={R.REO.requiresProtocol}, add=[(R.REO.requiresProtocol, Literal("true"))]),
                  "the protocol flag is the string \"true\", not an xsd:boolean"),
    }
    for cid, (ga, what) in ill.items():
        case(cid, "P13", "negative", "pending_review", decide1(ga),
             f"{what}: the contract must not choose a value or reinterpret it; the annotation goes to review")
    case("P13-e", "P13", "property", ["pending_review"] * 4, [oracle1(ga) for ga, _ in ill.values()],
         "the SPARQL encoding gives the same decisions on the four ill-formed annotations")
    one_ = Literal("1", datatype=rdflib.XSD.boolean)
    case("P13-f", "P13", "boundary", "conditional",
         decide1(annotation(drop={R.REO.requiresProtocol}, add=[(R.REO.requiresProtocol, one_)])),
         "\"1\"^^xsd:boolean is a well-typed true: R001 keeps its decision")
    case("P13-g", "P13", "regression",
         ["conditional", "accepted_with_reservations", "accepted", "accepted", "pending_review", "rejected",
          "conditional", "rejected", "accepted", "rejected"], [base[k].state for k in sorted(base)],
         "the ten example annotations are well formed: decisions of Table 9")

    # ------------------------------------------------------------------ P14: references resolved by IRI
    fa = annotation(drop={R.REO.hasResponsibleActor}, add=[(R.REO.hasResponsibleActor, OX["Physician"])])
    case("P14-a", "P14", "negative", ("rejected", "rejected"), (decide1(fa), oracle1(fa)),
         "the annotation names other:Physician as responsible actor; the model has only ex:Physician, so O1 "
         "finds the actor undefined (the SPARQL encoding agrees); requiresAuthorityOf still names ex:Physician")
    try:
        rfa = R.validate(env0, R.load_requirements(fa)[0])
        obs = (any("authority" in j and j.startswith("OK") for j in rfa.justifications),
               any("undefined" in j for j in rfa.justifications))
    except Exception as ex:
        obs = f"error: {type(ex).__name__}"
    case("P14-b", "P14", "boundary", (True, True), obs,
         "fields are resolved one by one: the authority check on ex:Physician still passes while the foreign "
         "actor is undefined")
    fc = annotation(add=[(R.REO.refersToConcept, OX["D001_Medication"])])
    case("P14-c", "P14", "negative", ("rejected", "rejected"), (decide1(fc), oracle1(fc)),
         "a second concept other:D001_Medication, not in the model, is undefined (O4)")
    case("P14-d", "P14", "regression", "conditional", decide1(annotation()),
         "the annotation of R001 as written uses the model's IRIs")

    # ------------------------------------------------------------------ P15: migration to an invalid successor
    def link(x, y):
        return lambda g: g.add((E[x], R.REO.supersededBy, E[y]))
    g15, env15 = variant(link("Physician", "G005"))
    case("P15-a", "P15", "negative", "pending_review", reassess_(env15, q1, R.supersession_map(g15)).state,
         "the Physician is declared superseded by a goal: the reference cannot be migrated to an element of "
         "another view; review, not a rejection for an undefined actor")
    g15, env15 = variant(link("D001_Medication", "D001_Missing"))
    case("P15-b", "P15", "negative", "pending_review", reassess_(env15, q1, R.supersession_map(g15)).state,
         "a successor that is not in the snapshot: review, not a rejection for an undefined concept")

    def ren_actor(g):
        for s_, p_, o_ in list(g.triples((E["Physician"], None, None))):
            g.remove((s_, p_, o_)); g.add((E["Physician2"], p_, o_))
        for s_, p_, o_ in list(g.triples((None, None, E["Physician"]))):
            g.remove((s_, p_, o_)); g.add((s_, p_, E["Physician2"]))
        g.add((E["Physician"], R.REO.supersededBy, E["Physician2"]))
    g15, env15 = variant(ren_actor)
    case("P15-c", "P15", "regression", "conditional", reassess_(env15, q1, R.supersession_map(g15)).state,
         "a valid rename of the actor migrates the three fields that name it")

    # ------------------------------------------------------------------ P16: premise history in records
    pfr = getattr(R, "previous_from_records", None)

    def readback(results, prev=None):
        if pfr is None:
            raise AttributeError("previous_from_records")
        return pfr(R.record_graph(results, previous=prev) if prev else R.record_graph(results))

    g_rm, env_rm = variant(lambda g: g.remove((U, None, None)))
    try:
        obs = reassess_(env_rm, q1, None, readback([prev])["R001"]).state
    except Exception as ex:
        obs = f"error: {type(ex).__name__}"
    case("P16-a", "P16", "negative", "pending_review", obs,
         "the previous assessment read back from its record: the open premise U001 is removed, so review (as P2-a)")
    try:
        obs = reassess_(env0, qs, None, readback([prev_soft])["T"]).state
    except Exception as ex:
        obs = f"error: {type(ex).__name__}"
    case("P16-b", "P16", "negative", "accepted_with_reservations", obs,
         "a soft premise read back from its record keeps its level: a reservation, not a review (as P2-h)")
    r2 = reassess_(env_rm, q1, None, prev)
    g3 = deepcopy(g_rm); g3.add((E["G001"], R.REO.priority, Literal(2)))
    env3 = R.project(g3)
    case("P16-c", "P16", "negative", "pending_review", reassess_(env3, q1, None, r2).state,
         "a later unrelated change after the removal of U001: its resolution is still not recorded, so the "
         "premise stays unresolved and R001 stays in review")
    try:
        obs = reassess_(env3, q1, None, readback([r2])["R001"]).state
    except Exception as ex:
        obs = f"error: {type(ex).__name__}"
    case("P16-d", "P16", "negative", "pending_review", obs, "the same, with the previous assessment read back")
    g4 = deepcopy(g3)
    for t in g0.triples((U, None, None)):
        g4.add(t)
    g4.remove((U, R.REO.status, None)); g4.add((U, R.REO.status, Literal("validated")))
    case("P16-e", "P16", "boundary", "accepted_with_reservations", reassess_(R.project(g4), q1, None, r2).state,
         "U001 comes back validated: its resolution is recorded and the premise is no longer unresolved")
    gnd = deepcopy(g_rm); gnd.remove((E["G002"], None, None))
    r_nd = reassess_(R.project(gnd), q1, None, prev)
    case("P16-f", "P16", "negative", ("not_derived", "pending_review"),
         (r_nd.state, reassess_(env_rm, q1, None, r_nd).state),
         "U001 and the seed goal G002 are removed together (not derived); when G002 comes back, U001's removal "
         "is still not a resolution")
    scen = {"P2-a": (env_rm, q1, prev), "P2-f": (R.project(grd), q1, prev_ref), "P2-h": (env0, qs, prev_soft),
            "P2-i": (R.project(_without(gsr, U9)), qs, prev_soft)}
    try:
        obs = sorted(k for k, (env_, q_, p_) in scen.items()
                     if reassess_(env_, q_, None, p_).state != reassess_(env_, q_, None, readback([p_])[q_.rid]).state)
    except Exception as ex:
        obs = f"error: {type(ex).__name__}"
    case("P16-g", "P16", "property", [], obs,
         "re-assessment from the record read back decides as re-assessment from the assessment in memory")

    # ------------------------------------------------------------------ P2-j/k: relevance of a removed premise
    G9 = E["G960_Sub"]
    qj = req(seed_goal="G960_Sub", actor="Physician", actions=["Action_Diagnose"])

    def sub_of(parent):
        def f(g):
            g.add((G9, RDF.type, R.REO.OperationalGoal)); g.add((G9, R.REO.refines, E[parent]))
        return f
    gj0, envj0 = variant(sub_of("G002"))
    prev_j = R.validate(envj0, qj)
    gj1 = deepcopy(gj0); gj1.remove((U, None, None)); gj1.remove((G9, R.REO.refines, None))
    gj1.add((G9, R.REO.refines, E["G001"]))
    envj1 = R.project(gj1)
    def reassess_env(env, q, p, penv):
        """Re-assessment with the previous snapshot; a release without that parameter is called without it."""
        try:
            return R.reassess(env, q, None, p, previous_env=penv).state
        except TypeError:
            return reassess_(env, q, None, p).state + " (previous snapshot not supported)"
        except AttributeError:
            return reassess_(env, q, None, p).state + " (no re-assessment with history)"
    obs = (prev_j.state, reassess_env(envj1, qj, prev_j, envj0))
    case("P2-j", "P2", "negative", ("conditional", R.validate(envj1, qj).state), obs,
         "U001 blocked G002, which the requirement no longer reaches after the same change; with the previous "
         "snapshot the removal is seen to bear on nothing the new assessment reads: no review")
    obs = reassess_env(env_rm, q1, prev, env0).split(" (")[0]
    case("P2-k", "P2", "boundary", "pending_review", obs,
         "with the previous snapshot, U001 still bears on G002, which R001 reads: review (as P2-a)")

    # ------------------------------------------------------------------ P17: shapes for records and annotations
    try:
        from pyshacl import validate as shacl
        sh_g = R.load_graph([shapes_dir / "reo-derivation-shapes.ttl"])

        def conforms(data):
            return shacl(data, shacl_graph=sh_g, ont_graph=onto_g, inference="rdfs")[0]
        rec_prev, rec_new = R.record_iri(prev), R.record_iri(rnew)
        hist = R.record_graph([prev]) + R.record_graph([rnew], previous={"R001": prev})
        dat = deepcopy(g0) + rq_g
        selfsup = R.record_graph([prev]); selfsup.add((rec_prev, R.REO.supersedes, rec_prev))
        other = R.record_graph([prev, base["R002"]])
        other.add((R.record_iri(base["R002"]), R.REO.supersedes, rec_prev))
        cyc = deepcopy(hist); cyc.add((rec_prev, R.REO.supersedes, rec_new))
        twoauth = annotation(add=[(R.REO.requiresAuthorityOf, E["Pharmacist"])])
        obs = [conforms(dat + x) for x in (selfsup, other, cyc, hist)] + [conforms(g0 + twoauth)]
    except Exception as ex:
        obs = f"error: {type(ex).__name__}: {ex}"
    case("P17-a", "P17", "negative", [False, False, False, True, False], obs,
         "the derivation shapes reject a record that supersedes itself, a record of another requirement, and a "
         "supersession cycle; they accept a proper history; they reject two requiresAuthorityOf values")

    ck = Path(checker).resolve()
    out = {"checker": ck.relative_to(ROOT).as_posix() if ck.is_relative_to(ROOT) else ck.name,
           "policy": getattr(R, "POLICY_VERSION", "?"),
           "cases": len(rows), "passed": sum(r["pass"] for r in rows), "rows": rows}
    Path(args.out).parent.mkdir(exist_ok=True, parents=True)
    Path(args.out).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    for r_ in rows:
        print(f"{'PASS' if r_['pass'] else 'FAIL'}  {r_['id']:6s} {r_['kind']:10s} expected={r_['expected']} observed={r_['observed']}")
    print(f"{out['passed']}/{out['cases']} passed  ({out['policy']})")
    return 0 if out["passed"] == out["cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
