#!/usr/bin/env python3
"""Repeated timing on the extended example model.

Two operations are measured separately: projecting a snapshot (RDF graph -> views), which is done
once per snapshot, and assessing one requirement (validate). Five batches are measured after a
warm-up; results are summarized by the median and interquartile range, together with the execution
environment. "Per decision" adds the projection amortized over the ten requirements, which is the
cost of reassessing all ten after a change. Run times depend on the machine and its load: they
characterize this environment only. Writes results/timing.json.

Options: --checker PATH times another checker module with the same procedure, and --out PATH sets the output
file, so that two implementations can be measured in the same run and environment.
"""
import argparse, importlib.util, json, os, platform, statistics, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ap = argparse.ArgumentParser()
ap.add_argument("--checker", default=str(ROOT / "reo_checker.py"))
ap.add_argument("--out", default=str(ROOT / "results" / "timing.json"))
ARGS = ap.parse_args()
_spec = importlib.util.spec_from_file_location("timed_checker", ARGS.checker)
R = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = R  # dataclasses need the module to be registered
_spec.loader.exec_module(R)  # noqa: E402

EXD = ROOT / "examples" / "medical-emergency"
G = R.load_graph([EXD / "hospital.ttl", EXD / "hospital-extension.ttl"])
env = R.project(G)
reqs = R.load_requirements(R.load_graph([EXD / "hospital-requirements.ttl"]))
BATCHES, REPS, WARM = 5, 200, 50
for _ in range(WARM):
    for q in reqs:
        R.validate(env, q)
per_req, per_set, batch_medians = [], [], []
for b in range(BATCHES):
    sets = []
    for _ in range(REPS):
        t0 = time.perf_counter()
        for q in reqs:
            R.validate(env, q)
        dt = (time.perf_counter() - t0) * 1000.0
        sets.append(dt)
    per_set += sets
    per_req += [s / len(reqs) for s in sets]
    batch_medians.append(statistics.median(sets) / len(reqs))
proj = []
for _ in range(BATCHES):
    for _ in range(40):
        t0 = time.perf_counter(); R.project(G); proj.append((time.perf_counter() - t0) * 1000.0)
qp = statistics.quantiles(proj, n=4)
q = statistics.quantiles(per_req, n=4)
per_decision = (statistics.median(proj) + statistics.median(per_set)) / len(reqs)
out = {"environment": {"python": platform.python_version(), "implementation": platform.python_implementation(),
                       "system": platform.system(), "release": platform.release(), "machine": platform.machine(),
                       "processor": platform.processor() or "unknown", "cpu_count": os.cpu_count()},
       "design": {"batches": BATCHES, "repetitions_per_batch": REPS, "warmup_sets": WARM, "requirements": len(reqs)},
       "ms_per_requirement": {"median": statistics.median(per_req), "q1": q[0], "q3": q[2],
                              "min": min(per_req), "max": max(per_req), "batch_medians": batch_medians},
       "ms_projection": {"median": statistics.median(proj), "q1": qp[0], "q3": qp[2], "n": len(proj)},
       "ms_per_decision_incl_projection": per_decision,
       "ms_per_set_of_ten": {"median": statistics.median(per_set), "q1": statistics.quantiles(per_set, n=4)[0],
                             "q3": statistics.quantiles(per_set, n=4)[2]}}
out["checker"] = str(Path(ARGS.checker).resolve().relative_to(ROOT)) if Path(ARGS.checker).resolve().is_relative_to(ROOT) else ARGS.checker
out["policy"] = getattr(R, "POLICY_VERSION", "unknown")
Path(ARGS.out).parent.mkdir(exist_ok=True)
Path(ARGS.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
print(json.dumps(out, indent=1))
