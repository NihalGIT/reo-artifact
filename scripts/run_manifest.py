#!/usr/bin/env python3
"""Reproducibility record of a pipeline run: software versions, seeds, commands, and SHA-256 digests of every
input and output file. Run as the last step of scripts/run_all.py; writes results/run_manifest.json.

The digests of results/*.json include fields that legitimately vary between runs (wall-clock timings in
timing.json, property_checks*.json, evaluation_summary.json and determinism.json); the decision outputs
do not vary (see results/determinism.json)."""
import hashlib, json, platform, subprocess, sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def files(*globs):
    out = {}
    for g in globs:
        for p in sorted(ROOT.glob(g)):
            if p.is_file() and "__pycache__" not in p.parts and p.name != "run_manifest.json":
                out[p.relative_to(ROOT).as_posix()] = sha(p)
    return out


def version(pkg):
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


def main():
    import reo_checker as R
    import run_all
    try:
        lines = subprocess.run(["java", "-version"], capture_output=True, text=True).stderr.splitlines()
        java = next((l for l in lines if "version" in l), None)   # skip "Picked up JAVA_TOOL_OPTIONS" lines
    except OSError:
        java = None
    out = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy": R.POLICY_VERSION,
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "packages": {p: version(p) for p in ("rdflib", "pyshacl", "owlready2", "owlrl")},
                        "java": java},
        "seeds": {"property_checks.py (V1-V3)": 20261103, "property_check_v4.py (V4)": 20261105,
                  "determinism_check.py (PYTHONHASHSEED)": [0, 1, 4242],
                  "run_evaluation.py": "no randomness (corpus generated deterministically from the example)"},
        "commands": [" ".join(["python"] + cmd) for _, cmd in run_all.STEPS],
        "inputs": files("ontology/*.ttl", "examples/**/*.ttl", "evaluation/extension/*.ttl", "reo_checker.py",
                        "prototype.py", "scripts/*.py", "evaluation/llm-evaluation/raw/*", "requirements.txt"),
        "outputs": files("results/*", "generated/*", "evaluation/llm-evaluation/results/*"),
    }
    (ROOT / "results" / "run_manifest.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"manifest: {len(out['inputs'])} inputs, {len(out['outputs'])} outputs, policy {out['policy']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
