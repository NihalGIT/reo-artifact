#!/usr/bin/env python3
"""Run the complete verification and evaluation pipeline of REO v1.0.0.

Usage:  python scripts/run_all.py [--skip-corpus]
The corpus evaluation (scripts/run_evaluation.py) takes about 10-15 minutes
on two cores; --skip-corpus reuses results/evaluation_summary.json.
Java (for HermiT) is required by scripts/reasoner_check.py."""
import json, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
STEPS = [
    ("Turtle syntax", ["scripts/validate_syntax.py"]),
    ("Artifact metrics and vocabulary inventory", ["scripts/artifact_metrics.py"]),
    ("Pitfall scan (OOPS! catalogue subset)", ["scripts/pitfall_scan.py"]),
    ("HermiT consistency and injected errors", ["scripts/reasoner_check.py"]),
    ("SHACL validation", ["scripts/run_shacl.py"]),
    ("Competency questions", ["scripts/run_competency_questions.py"]),
    ("Regression scenarios and targeted regressions", ["prototype.py"]),
    ("Seven-view derivation criteria", ["scripts/view_derivation.py"]),
    ("Identity migration cases", ["scripts/run_migration.py"]),
    ("Records and annotations outside the environment snapshot", ["scripts/check_record_isolation.py"]),
    ("Extension contract (vocabulary extension)", ["scripts/run_extension_check.py"]),
    ("Corner cases (REO v1.0.0)", ["scripts/check_corrections.py"]),
    ("Implementation-property checks (random mutation generator)", ["scripts/property_checks.py"]),
    ("Trace sufficiency on random base snapshots (V4)", ["scripts/property_check_v4.py"]),
    ("Annotation perturbation (sensitivity of decisions to annotation errors)", ["scripts/annotation_perturbation.py"]),
    ("Timing (repeated measurements)", ["scripts/measure_timing.py"]),
    ("Mutation-corpus evaluation", ["scripts/run_evaluation.py"]),
    ("Premise-removal rule on the corpus (reassessment with the previous assessment)", ["scripts/history_rule_corpus.py"]),
    ("Views of the behaviour-derived relevant pairs", ["scripts/view_relevance.py"]),
    ("TF-IDF threshold sensitivity of text-similarity trace selection", ["scripts/ir_sensitivity.py"]),
    ("Language-model evaluation (re-score frozen outputs)", ["evaluation/llm-evaluation/scripts/rescore_all.py"]),
    ("Determinism across Python hash seeds", ["scripts/determinism_check.py"]),
    ("Ontology diagram", ["scripts/draw_ontology.py"]),
    ("Paper macros and tables", ["scripts/export_paper_macros.py"]),
    ("Run manifest (versions, seeds, commands, file digests)", ["scripts/run_manifest.py"]),
]

def main():
    skip = "--skip-corpus" in sys.argv
    failed = []
    for name, cmd in STEPS:
        if skip and cmd[0].endswith("run_evaluation.py"):
            print(f"[skip] {name}"); continue
        t = time.time()
        r = subprocess.run([sys.executable] + cmd, cwd=ROOT, capture_output=True, text=True)
        # An older checker must fail the defect cases and pass every regression case.
        expected_fail = "--old" in cmd
        ok = (r.returncode == 0) != expected_fail
        if ok and expected_fail:
            rows = json.loads((ROOT / cmd[cmd.index("--out") + 1]).read_text(encoding="utf-8"))["rows"]
            ok = all(x["pass"] for x in rows if x["kind"] == "regression")
        print(f"[{'ok' if ok else 'FAILED'}] {name} ({time.time() - t:.0f}s)")
        if expected_fail and ok:
            print("      " + (r.stdout.strip().splitlines() or [""])[-1])
        if not ok:
            failed.append(name); print(r.stdout[-2000:], r.stderr[-2000:])
    print("All steps passed." if not failed else f"Failed steps: {failed}")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
