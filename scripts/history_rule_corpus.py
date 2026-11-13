#!/usr/bin/env python3
"""Effect of the premise-removal rule of re-assessment on the mutation corpus.

For every mutant of the corpus (scripts/run_evaluation.py, same generator) and every requirement, compares
the fresh assessment of the mutated snapshot (validate, as used by the corpus evaluation) with the
re-assessment that also receives the previous assessment (reassess with previous = assessment on the
unmutated model). Reports how many decisions the rule changes and the state transitions, and checks
Proposition 2 for re-assessment: a re-assessment differs from the previous assessment only if a traced
element is touched (touched identifiers recomputed from the mutant's triples). The corpus metrics of the
paper are computed with the fresh assessment; this script adds the history-aware view.
Writes results/history_rule_corpus.json.
"""
import json, sys
from collections import Counter
from pathlib import Path

import rdflib
from rdflib import RDF, URIRef

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import reo_checker as R  # noqa: E402
import run_evaluation as E  # noqa: E402

g0 = R.load_graph(E.ENV_FILES)
reqs = R.load_requirements(R.load_graph([E.REQ_FILE]))
env0 = R.project(g0)
base = {q.rid: R.validate(env0, q) for q in reqs}
muts = E.generate_corpus(g0)
pairs = fresh_changes = hist_changes = differ = viol = 0
trans, ops, removed_premises = Counter(), Counter(), Counter()
examples = []
for m in muts:
    g = rdflib.Graph()
    for t in g0:
        g.add(t)
    for t in m["remove"]:
        g.remove(t)
    for t in m["add"]:
        g.add(t)
    env = R.project(g)
    touched = {R.local(s) for s, _, _ in list(m["remove"]) + list(m["add"])}
    touched |= {R.local(o) for _, p, o in list(m["remove"]) + list(m["add"]) if isinstance(o, URIRef) and p != RDF.type}
    for q in reqs:
        pairs += 1
        f = R.validate(env, q)
        h = R.reassess(env, q, previous=base[q.rid])
        fresh_changes += f.state != base[q.rid].state
        hist_changes += h.state != base[q.rid].state
        changed = (h.state, sorted(h.justifications)) != (base[q.rid].state, sorted(base[q.rid].justifications))
        viol += changed and not (base[q.rid].trace & touched)
        if h.state != f.state:
            differ += 1
            trans[f"{base[q.rid].state} -> fresh {f.state} / with history {h.state}"] += 1
            ops[m["op"]] += 1
            for j in h.justifications:
                if "no longer exists" in j:
                    removed_premises[j.split()[3]] += 1
            if len(examples) < 5:
                examples.append({"mutant": m["id"], "desc": m["desc"], "rid": q.rid, "base": base[q.rid].state,
                                 "fresh": f.state, "with_history": h.state})
out = {"policy": R.POLICY_VERSION, "mutants": len(muts), "pairs": pairs,
       "decision_changes_fresh": fresh_changes, "decision_changes_with_history": hist_changes,
       "pairs_where_rule_changes_decision": differ, "transitions": dict(trans.most_common()),
       "by_operator": dict(ops.most_common()), "removed_premises": dict(removed_premises.most_common()),
       "prop2_violations_reassessment": viol, "examples": examples}
(ROOT / "results").mkdir(exist_ok=True)
(ROOT / "results" / "history_rule_corpus.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print(json.dumps(out, indent=1))
sys.exit(0 if viol == 0 else 1)
