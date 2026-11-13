#!/usr/bin/env python3
"""Compare two result folders of the pipeline (for example the committed results and a fresh run).

Usage:
    python scripts/compare_results.py REF_DIR NEW_DIR [--generated REF_GEN NEW_GEN] [--label-map MAP.json]
                                      [--out OUT.json]

Every JSON, CSV.GZ and text file present in either folder is compared. Run times are machine-dependent and
are excluded: the files timing*.json, run_manifest.json (environment, dates and file digests) and run_all.log (step
durations), and the JSON
fields named ``seconds`` or ``runtime_seconds`` or containing ``ms`` as a word part (for example
``mean_validate_ms_per_requirement``). In the generated LaTeX macros, the macros derived from run times
(names containing ``Ms``, ``Speedup`` or ``TimingPython``) are excluded. Everything else must be equal.

``--label-map`` takes a JSON file {"files": {old_name: new_name}, "strings": [[old, new], ...]}: files of
REF_DIR are matched under their new names, and the string replacements are applied, in order, to the keys,
string values and text of REF_DIR before the comparison. It is used to compare runs made before and after a
renaming of labels; every replacement is listed in the output.

Exit status: 0 if no difference remains, 1 otherwise. The report lists every remaining difference.
"""
import argparse, gzip, json, re, sys
from pathlib import Path

VOLATILE_FILES = re.compile(r"^(timing.*\.json|run_manifest\.json|run_all\.log)$")
VOLATILE_KEY = re.compile(r"^(seconds|runtime_seconds)$|(^|_)ms(_|$)")
VOLATILE_MACRO = re.compile(r"\\newcommand\{\\[A-Za-z]*(Ms|Speedup|TimingPython)[A-Za-z]*\}")


def relabel(text, pairs):
    for old, new in pairs:
        text = text.replace(old, new)
    return text


def norm(obj, pairs):
    if isinstance(obj, dict):
        return {relabel(k, pairs): norm(v, pairs) for k, v in obj.items() if not VOLATILE_KEY.search(k)}
    if isinstance(obj, list):
        return [norm(v, pairs) for v in obj]
    if isinstance(obj, str):
        return relabel(obj, pairs)
    return obj


def flat(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from flat(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from flat(v, f"{path}[{i}]")
    else:
        yield path, obj


def read(path):
    data = gzip.open(path).read() if path.suffix == ".gz" else path.read_bytes()
    return data.decode("utf-8")


def compare_dir(ref_dir, new_dir, files_map, pairs, report, kind):
    ref_files = {files_map.get(p.name, p.name): p for p in ref_dir.iterdir() if p.is_file()}
    new_files = {p.name: p for p in new_dir.iterdir() if p.is_file()}
    for name in sorted(set(ref_files) | set(new_files)):
        entry = {"folder": kind, "file": name}
        if VOLATILE_FILES.match(name):
            entry["status"] = "excluded (run times, environment, digests)"
        elif name not in ref_files or name not in new_files:
            entry["status"] = "only in " + ("new" if name in new_files else "reference")
        elif name.endswith(".json"):
            a = dict(flat(norm(json.loads(read(ref_files[name])), pairs)))
            b = dict(flat(norm(json.loads(read(new_files[name])), [])))
            diff = sorted(k for k in set(a) | set(b) if a.get(k, "<missing>") != b.get(k, "<missing>"))
            entry["status"] = "equal" if not diff else f"{len(diff)} differing values"
            entry["differences"] = [{"path": k, "reference": a.get(k, "<missing>"), "new": b.get(k, "<missing>")}
                                    for k in diff[:50]]
        else:
            a = relabel(read(ref_files[name]), pairs).splitlines()
            b = read(new_files[name]).splitlines()
            if kind == "generated":
                a = [l for l in a if not VOLATILE_MACRO.search(l)]
                b = [l for l in b if not VOLATILE_MACRO.search(l)]
            entry["status"] = "equal" if a == b else "different"
            if a != b:
                entry["differences"] = [{"line": i + 1, "reference": x, "new": y}
                                        for i, (x, y) in enumerate(zip(a, b)) if x != y][:50]
                if len(a) != len(b):
                    entry["differences"].append({"line_count": [len(a), len(b)]})
        report.append(entry)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ref_dir")
    ap.add_argument("new_dir")
    ap.add_argument("--generated", nargs=2, metavar=("REF_GEN", "NEW_GEN"))
    ap.add_argument("--label-map")
    ap.add_argument("--out")
    args = ap.parse_args()
    lm = json.loads(Path(args.label_map).read_text()) if args.label_map else {}
    files_map, pairs = lm.get("files", {}), [tuple(p) for p in lm.get("strings", [])]
    report = []
    compare_dir(Path(args.ref_dir), Path(args.new_dir), files_map, pairs, report, "results")
    if args.generated:
        compare_dir(Path(args.generated[0]), Path(args.generated[1]), files_map, pairs, report, "generated")
    bad = [e for e in report if not (e["status"] == "equal" or e["status"].startswith("excluded"))]
    out = {"reference": args.ref_dir, "new": args.new_dir, "label_map": lm or None,
           "files_compared": len(report), "files_with_differences": len(bad), "files": report}
    text = json.dumps(out, indent=1, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    for e in report:
        print(f"{e['folder']}/{e['file']}: {e['status']}")
    print(f"{len(bad)} file(s) with differences")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
