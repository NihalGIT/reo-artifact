#!/usr/bin/env python3
"""Sensitivity of the decision to single-field errors in the annotation alpha(q).

The contract decides on the annotation alpha(q), not on the statement q (the text is not parsed). This
experiment measures how a wrong annotation shows up in the decision. Reference: the annotations of the ten
requirements of the running example as written by the authors (assumed correct; no other ground truth).
Each perturbation changes one field of one annotation:

  drop  remove one reference (actor, authority holder, action, concept, property, interaction, expected source,
        declared constraint);
  swap  replace one reference, the seed goal or the context by another element of the same view of the model;
  add   add one element of the field's view to a multi-valued field (action, concept, property, declared hard or
        soft constraint);
  flag  toggle the protocol requirement.

Every perturbed annotation is assessed on the extended model. Outcomes per perturbation: unchanged decision
(the error is not visible in the decision); changed from an acceptance-type state (accepted, accepted with
reservations, conditional) to review, rejection or not derived (the error becomes visible); a weaker acceptance;
a more permissive state (ranking accepted > accepted with reservations > conditional > pending review > rejected
> not derived), which hides a problem; or a change between review, rejection and not derived. The perturbations are synthetic; they are not a sample of real annotation errors.
Writes results/annotation_perturbation.json.
"""
import json, sys
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import reo_checker as R  # noqa: E402

EXD = ROOT / "examples" / "medical-emergency"
RANK = {R.ACCEPTED: 5, R.ACCEPTED_WITH_RESERVATIONS: 4, R.CONDITIONAL: 3, R.PENDING_REVIEW: 2, R.REJECTED: 1,
        R.NOT_DERIVED: 0}
SINGLE = ["seed_goal", "context", "actor", "authority_of", "interaction", "interaction_source"]
MULTI = ["actions", "concepts", "properties", "hard", "soft"]


def perturbations(q, env):
    out = []
    for f in SINGLE:
        v = getattr(q, f)
        view = R.FIELD_VIEW[f]
        if v and f not in ("seed_goal", "context"):
            q2 = deepcopy(q); setattr(q2, f, None); out.append(("drop", f, f"{v} -> none", q2))
        if v:
            for alt in sorted(env.views[view]):
                if alt != v:
                    q2 = deepcopy(q); setattr(q2, f, alt); out.append(("swap", f, f"{v} -> {alt}", q2))
    for f in MULTI:
        vals = getattr(q, f)
        view = R.FIELD_VIEW[f]
        for v in vals:
            q2 = deepcopy(q); setattr(q2, f, [x for x in vals if x != v]); out.append(("drop", f, f"{v} -> none", q2))
            for alt in sorted(env.views[view]):
                if alt not in vals:
                    q2 = deepcopy(q); setattr(q2, f, sorted([x for x in vals if x != v] + [alt]))
                    out.append(("swap", f, f"{v} -> {alt}", q2))
        for alt in sorted(env.views[view]):
            if alt not in vals:
                q2 = deepcopy(q); setattr(q2, f, sorted(vals + [alt])); out.append(("add", f, f"+{alt}", q2))
    q2 = deepcopy(q); q2.requires_protocol = not q.requires_protocol
    out.append(("flag", "requires_protocol", f"{q.requires_protocol} -> {q2.requires_protocol}", q2))
    return out


NEG = {R.PENDING_REVIEW, R.REJECTED, R.NOT_DERIVED}


def outcome(ref, new):
    if new == ref:
        return "unchanged"
    if new in NEG:
        return "between review, rejection, not derived" if ref in NEG else "to review, rejection or not derived"
    if RANK[new] > RANK[ref]:
        return "more permissive"
    return "weaker acceptance"


def main():
    g0 = R.load_graph([EXD / "hospital.ttl", EXD / "hospital-extension.ttl"])
    env = R.project(g0)
    reqs = R.load_requirements(R.load_graph([EXD / "hospital-requirements.ttl"]))
    rows = []
    for q in reqs:
        q = deepcopy(q); q.field_iris = {}; q.ref_iris = {}   # perturbed values are local names of model elements
        ref = R.validate(env, q).state
        for op, f, what, q2 in perturbations(q, env):
            st = R.validate(env, q2).state
            rows.append({"requirement": q.rid, "reference": ref, "operator": op, "field": f, "change": what,
                         "state": st, "outcome": outcome(ref, st)})
    by = lambda key: {k: dict(Counter(r["outcome"] for r in rows if key(r) == k))
                      for k in sorted({key(r) for r in rows})}
    trans = Counter((r["reference"], r["state"]) for r in rows)
    group = lambda r: "negative reference" if r["reference"] in NEG else "acceptance-type reference"
    permissive = [r for r in rows if r["outcome"] == "more permissive"]
    out = {"requirements": len(reqs), "perturbations": len(rows),
           "outcomes": dict(Counter(r["outcome"] for r in rows)),
           "by_operator": by(lambda r: r["operator"]), "by_field": by(lambda r: r["field"]),
           "by_requirement": by(lambda r: r["requirement"]),
           "by_reference_group": by(group),
           "requirements_by_group": {k: sorted({r["requirement"] for r in rows if group(r) == k})
                                     for k in sorted({group(r) for r in rows})},
           "transitions": {f"{a}->{b}": n for (a, b), n in sorted(trans.items())},
           "more_permissive_by_field_operator": dict(Counter(f"{r['operator']}:{r['field']}" for r in permissive)),
           "more_permissive_examples": permissive[:15], "rows": rows}
    (ROOT / "results" / "annotation_perturbation.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("requirements", "perturbations", "outcomes", "by_reference_group",
                                          "requirements_by_group")}, indent=1))
    print("more permissive by field/operator:", out["more_permissive_by_field_operator"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
