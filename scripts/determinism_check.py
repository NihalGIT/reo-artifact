#!/usr/bin/env python3
"""Determinism of the decision-producing steps across Python hash seeds.

Python randomizes the iteration order of sets and dictionaries of strings per process (PYTHONHASHSEED). A
result that depended on that order (for example a silent choice among several values) would differ between
runs. This script recomputes, in fresh processes with different PYTHONHASHSEED values:

  example   decision, sorted justifications and trace of the ten requirements of the running example;
  records   the canonical N-Triples of their PROV-O records;
  corpus    checker and SPARQL-encoding decisions on every k-th mutant of the mutation corpus;
  e1        checker and SPARQL-encoding decisions, justifications and traces of the frozen E1 suites;

and compares SHA-256 digests. Property: one digest per component. Writes results/determinism.json.

    python scripts/determinism_check.py [--seeds 0 1 4242] [--stride 40]
"""
import argparse, hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHILD = r'''
import hashlib, json, sys
from pathlib import Path
ROOT = Path(sys.argv[1]); stride = int(sys.argv[2])
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import rdflib
import reo_checker as R
import sparql_oracle as O
import run_evaluation as RE
EXD = ROOT / "examples" / "medical-emergency"
g0 = R.load_graph([EXD / "hospital.ttl", EXD / "hospital-extension.ttl"])
rq = R.load_graph([EXD / "hospital-requirements.ttl"])
reqs = R.load_requirements(rq)
env0 = R.project(g0)
res = [R.validate(env0, q) for q in reqs]
def h(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()
out = {"example": h([[r.rid, r.state, sorted(r.justifications), sorted(r.trace)] for r in res])}
rg = R.record_graph(res)
out["records"] = hashlib.sha256("\n".join(sorted(f"{s.n3()} {p.n3()} {o.n3()} ." for s, p, o in rg)).encode()).hexdigest()
onto = R.load_graph(R.ONTOLOGY_FILES)
corpus = RE.generate_corpus(g0)
rows = []
for m in corpus[::stride]:
    g = rdflib.Graph()
    for t in g0: g.add(t)
    for t in m["remove"]: g.remove(t)
    for t in m["add"]: g.add(t)
    env = R.project(g)
    og = rdflib.Graph()
    for part in (onto, g, rq):
        for t in part: og.add(t)
    orc = O.decide(og)
    rows.append([m["id"], [[q.rid, R.validate(env, q).state, orc.get(q.rid)] for q in reqs]])
out["corpus"] = h(rows); out["corpus_mutants"] = len(rows)
print(json.dumps(out))
'''


def e1_digest(env):
    here = ROOT / "evaluation" / "llm-evaluation"
    with tempfile.TemporaryDirectory() as d:
        o = Path(d) / "e1.json"
        subprocess.run([sys.executable, str(here / "scripts" / "run_llm_suite.py"), str(o),
                        *map(str, sorted((here / "raw").glob("E1_*_suite.json")))],
                       cwd=here, env=env, check=True, capture_output=True)
        run = json.loads(o.read_text())
    items = [[a["author_code"], s["id"], it["requirement"], it.get("checker"), it.get("oracle"),
              sorted(it.get("justifications") or []), it.get("trace")]
             for a in run["authors"] for s in a["scenarios"] for it in s["items"]]
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest(), len(items)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", nargs="+", default=["0", "1", "4242"])
    ap.add_argument("--stride", type=int, default=40)
    ap.add_argument("--out", default=str(ROOT / "results" / "determinism.json"))
    a = ap.parse_args()
    runs = {}
    for seed in a.seeds:
        env = dict(os.environ, PYTHONHASHSEED=seed)
        r = subprocess.run([sys.executable, "-c", CHILD, str(ROOT), str(a.stride)], env=env, cwd=ROOT,
                           capture_output=True, text=True, check=True)
        runs[seed] = json.loads(r.stdout.strip().splitlines()[-1])
        runs[seed]["e1"], runs[seed]["e1_items"] = e1_digest(env)
    comps = ["example", "records", "corpus", "e1"]
    summary = {c: len({runs[s][c] for s in runs}) for c in comps}
    out = {"seeds": a.seeds, "stride": a.stride, "corpus_mutants": runs[a.seeds[0]]["corpus_mutants"],
           "e1_items": runs[a.seeds[0]]["e1_items"], "distinct_digests": summary,
           "deterministic": all(v == 1 for v in summary.values()), "runs": runs}
    Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("seeds", "corpus_mutants", "e1_items", "distinct_digests", "deterministic")}))
    return 0 if out["deterministic"] else 1


if __name__ == "__main__":
    sys.exit(main())
