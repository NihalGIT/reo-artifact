#!/usr/bin/env python3
"""Distribution over the seven views of the behaviour-derived relevant requirement-element pairs.

Input: results/evaluation_summary.json (trace_relevance.relevant: for each requirement, the elements whose
own description, changed alone, changes its assessment; Section VIII-D of the paper). Each relevant element
is mapped to its view in the running example (auxiliary classes: actions with A, domain properties and
invariants with D). The analysis shows which views the decisions of the example actually depend on. It is a
property of one example and one mutation corpus, not evidence that the seven views are necessary in general.
Writes results/view_relevance.json.
"""
import json, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import reo_checker as R  # noqa: E402

EXD = ROOT / "examples" / "medical-emergency"
LETTER = {"actors": "A", "actions": "A", "goals": "G", "domain": "D", "properties": "D", "invariants": "D",
          "constraints": "C", "contexts": "X", "uncertainties": "U", "interactions": "I"}


def main():
    env = R.project(R.load_graph([EXD / "hospital.ttl", EXD / "hospital-extension.ttl"]))
    rel = json.loads((ROOT / "results" / "evaluation_summary.json").read_text())["trace_relevance"]["relevant"]
    pairs, elements, per_req, unknown = Counter(), {}, {}, []
    for rid, els in sorted(rel.items()):
        views = set()
        for e in els:
            v = env.view_of(e)
            if v is None:
                unknown.append([rid, e]); continue
            pairs[LETTER[v]] += 1; elements.setdefault(LETTER[v], set()).add(e); views.add(LETTER[v])
        per_req[rid] = "".join(sorted(views, key="AGDCXUI".index))
    out = {"relevant_pairs": sum(len(v) for v in rel.values()),
           "pairs_by_view": {v: pairs.get(v, 0) for v in "AGDCXUI"},
           "elements_by_view": {v: len(elements.get(v, ())) for v in "AGDCXUI"},
           "views_per_requirement": per_req,
           "requirements_by_number_of_views": dict(sorted(Counter(len(v) for v in per_req.values()).items())),
           "views_with_relevant_pairs": sum(1 for v in "AGDCXUI" if pairs.get(v)),
           "unmapped": unknown}
    (ROOT / "results" / "view_relevance.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("relevant_pairs", "pairs_by_view", "elements_by_view",
                                          "requirements_by_number_of_views", "unmapped")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
