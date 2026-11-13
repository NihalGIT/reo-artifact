#!/usr/bin/env python3
"""Copy the generated tables and the derivation-module figure of the replication package into
supplement/inputs, and write the rows of Table S5 from the recorded results (no number typed by hand)."""
import json, shutil
from pathlib import Path
HERE = Path(__file__).resolve().parent
# Inside the replication package (supplementary/), the package root is the parent folder; in the manuscript
# repository (manuscript/supplement/), the package is ../../reo-artifact.
ART = HERE.parent if (HERE.parent / "reo_checker.py").exists() else HERE.parents[1] / "reo-artifact"
OUT = HERE / "inputs"
OUT.mkdir(exist_ok=True)
for f in ("tab_objprops.tex", "tab_dataprops.tex", "tab_llm_e1.tex", "tab_llm_e2.tex", "tab_migration.tex", "tab_operators.tex"):
    shutil.copy(ART / "generated" / f, OUT / f)
shutil.copy(ART / "figures" / "reo-derivation.pdf", OUT / "reo-derivation.pdf")
# Table S12: schema-only validation versus contract decisions (SHACL verdicts written out in the second column)
verdict = {"acc": "accept", "cond": "conditional", "rej": "reject"}
conf = []
for line in (ART / "generated" / "tab_shacl_confusion.tex").read_text(encoding="utf-8").splitlines():
    cells = line.split("&")
    if len(cells) > 2:
        cells[1] = " " + verdict[cells[1].strip()] + " "
    conf.append("&".join(cells))
(OUT / "tab_shacl_confusion.tex").write_text("\n".join(conf) + "\n", encoding="utf-8")
ev = json.load(open(ART / "results" / "evaluation_summary.json"))
m = ev["trace_accuracy"]["methods"] if "trace_accuracy" in ev else None
if m is None:  # locate the block that holds the trace methods
    def find(d):
        if isinstance(d, dict):
            if "recorded_fine_trace" in d:
                return d
            for v in d.values():
                r = find(v)
                if r is not None:
                    return r
        return None
    m = find(ev)
irs = json.load(open(ART / "results" / "ir_sensitivity.json"))
corp = ev["corpus"]
(OUT / "tab_operators_total.tex").write_text(f"Total & & {corp['mutants']} & {corp['mutants_changing_some_decision']} \\\\\n", encoding="utf-8")
f2 = lambda x: f"{x:.2f}"
rows = [f"Recorded read-set trace (this work) & -- & {f2(m['recorded_fine_trace']['precision'])} & {f2(m['recorded_fine_trace']['recall'])} & {f2(m['recorded_fine_trace']['f1'])} & -- & -- \\\\",
        f"View-level trace (elements of the seven views) & -- & {f2(m['coarse_trace']['precision'])} & {f2(m['coarse_trace']['recall'])} & {f2(m['coarse_trace']['f1'])} & -- & -- \\\\"]
for tau in sorted(irs["assessment"]):
    t = m[f"IR_tfidf_tau_{tau}"]
    a = irs["assessment"][tau]
    rows.append(f"TF-IDF text similarity & {tau} & {f2(t['precision'])} & {f2(t['recall'])} & {f2(t['f1'])} & "
                f"{round(100 * a['miss_rate'])}\\% & {a['mean_revalidations']:.2f} \\\\")
(OUT / "tab_trace_grid.tex").write_text("\n".join(rows) + "\n", encoding="utf-8")
print("inputs written:", sorted(p.name for p in OUT.iterdir()))

# --- Table S9: versions, read from the files of the replication package -------------------------
import re
def grab(path, pattern, group=1):
    m = re.search(pattern, (ART / path).read_text(encoding="utf-8"))
    return m.group(group) if m else None
core_v = grab("ontology/reo-core.ttl", r'owl:versionInfo "([^"]+)"')
core_iri = grab("ontology/reo-core.ttl", r'owl:versionIRI <([^>]+)>')
der_v = grab("ontology/reo-derivation.ttl", r'owl:versionInfo "([^"]+)"')
der_iri = grab("ontology/reo-derivation.ttl", r'owl:versionIRI <([^>]+)>')
dsh_v = grab("ontology/reo-derivation-shapes.ttl", r'DERIVATION SHAPES\s+.\s+(?:version )?(REO v[0-9.]+|[0-9.]+)')
pol = grab("reo_checker.py", r'POLICY_VERSION = "([^"]+)"')
orc = grab("scripts/sparql_oracle.py", r'Policy (ecre-policy-[0-9.]+)')
pkg = grab("CITATION.cff", r'\nversion: ([0-9.]+)')
ex_label = grab("examples/medical-emergency/hospital.ttl", r'\(REO (v[0-9.]+)\)')
shapes_label = grab("ontology/reo-shapes.ttl", r'version ([0-9.]+)')
man = json.load(open(ART / "results" / "run_manifest.json"))
env = man["environment"]
cq = sorted(p.name for p in (ART / "evaluation" / "competency-questions").glob("cq_suite*.json"))
nsteps = len(re.findall(r'^    \("', (ART / "scripts" / "run_all.py").read_text(encoding="utf-8"), re.M))
# vocabulary of the frozen specification pack of the language-model studies, compared with the modules of the package
from rdflib import Graph, RDF, OWL, URIRef
def declared(path):
    g = Graph(); g.parse(path)
    return {s for t in (OWL.ObjectProperty, OWL.DatatypeProperty, OWL.Class) for s in g.subjects(RDF.type, t) if isinstance(s, URIRef)}
pack_missing = sorted(str(x).split("#")[-1] for x in declared(ART / "ontology" / "reo-derivation.ttl") - declared(ART / "evaluation" / "llm-evaluation" / "spec" / "reo-derivation.ttl"))
pack_extra = sorted(str(x).split("#")[-1] for x in declared(ART / "evaluation" / "llm-evaluation" / "spec" / "reo-derivation.ttl") - declared(ART / "ontology" / "reo-derivation.ttl"))
assert not pack_extra, pack_extra
pack_note = ("specification packs and outputs of E1--E3, frozen and hashed before execution; the derivation module of the pack lacks "
             + str(len(pack_missing)) + " properties of decision records (" + ", ".join("\\texttt{" + x + "}" for x in pack_missing)
             + "), which the studies do not use; the example files are identical")
tt = lambda x: "\\texttt{" + x.replace("_", "\\_") + "}"
rows = [
 ("Replication package", f"REO v{pkg}", "\\texttt{CITATION.cff}, \\texttt{CHANGELOG.md}; to be deposited on Zenodo with a version DOI (not yet deposited)"),
 ("Core ontology \\texttt{reo-core.ttl}", core_v, "version IRI \\url{" + core_iri + "}"),
 ("Derivation module \\texttt{reo-derivation.ttl}", der_v, "version IRI \\url{" + der_iri + "}"),
 ("Core shapes \\texttt{reo-shapes.ttl}", "no version label in the file" if not shapes_label else shapes_label, "validated with the ontology modules and RDFS inference"),
 ("Derivation shapes \\texttt{reo-derivation-shapes.ttl}", dsh_v, "record history and annotation well-formedness shapes"),
 ("Reference checker \\texttt{reo\\_checker.py}", f"REO v{pkg}", f"implements policy \\texttt{{{pol}}}"),
 ("SPARQL encoding \\texttt{sparql\\_oracle.py}", f"REO v{pkg}", f"implements policy \\texttt{{{orc}}}"),
 ("Contract policy", f"\\texttt{{{pol}}}", "written into every decision record (\\texttt{reo:policyVersion})"),
 ("Evaluation scripts", f"REO v{pkg}", f"\\texttt{{scripts/run\\_all.py}} ({nsteps} steps); run manifest of {man['generated'][:10]}"),
 ("Example and fixtures", f"labelled REO {ex_label}", "synthetic medical-emergency example, extension, ten requirements, three SHACL fixtures"),
 ("Competency-question suite", ", ".join(c.replace('_', '\\_') for c in cq), "34 questions"),
 ("Language-model study packs", "frozen before the studies", pack_note),
 ("Software of the recorded run", f"Python {env['python']}", f"RDFLib {env['packages']['rdflib']}, pySHACL {env['packages']['pyshacl']}, Owlready2 {env['packages']['owlready2']}, OpenJDK {env['java'].split(chr(34))[1].split('.')[0]}"),
]
assert all(r[1] for r in rows), rows
t = ["\\begin{table}[H]",
     "\\caption{Versions of the components of the replication package of REO v1.0.0, read from the files (\\texttt{supplement/make\\_inputs.py}).}",
     "\\small", "\\begin{tabular}{@{}L{5.0cm}L{3.6cm}L{7.6cm}@{}}", "\\toprule",
     "\\textbf{Component} & \\textbf{Version} & \\textbf{Note} \\\\", "\\midrule"]
t += [f"{a} & {b} & {c} \\\\" for a, b, c in rows]
t += ["\\bottomrule", "\\end{tabular}", "\\end{table}"]
(OUT / "tab_versions.tex").write_text("\n".join(t) + "\n", encoding="utf-8")
print("tab_versions.tex:", len(rows), "rows")

# --- numbers of the article's sections and tables, read from the compiled article (build/access.aux) ---
AUX = HERE.parent / "build" / "access.aux"
if not AUX.exists():  # inside the replication package: keep the stored numbers of the article
    print("article_refs.tex kept (no compiled article here)")
    raise SystemExit(0)
aux = AUX.read_text(encoding="utf-8")
def num(label):
    m = re.search(r"\\newlabel\{" + re.escape(label) + r"\}\{\{(.*?)\}\{", aux)
    if not m:
        raise SystemExit("label not found in access.aux: " + label)
    return re.sub(r"\\mbox\s*\{(.*?)\}", r"\1", m.group(1)).strip()
want = {"ArtSecReo": "sec:reo", "ArtSecExtension": "sec:extension", "ArtSecEvaluation": "sec:evaluation",
        "ArtSecContract": "sec:eval-contract", "ArtSecImpact": "sec:eval-impact", "ArtAppRepro": "app:repro",
        "ArtTabFeatures": "tab:features", "ArtTabImpact": "tab:impact", "ArtSecEvalDesign": "sec:eval-design",
        "ArtPropSufficiency": "prop:sufficiency", "ArtSecViews": "sec:envmodel", "ArtSecShacl": "sec:eval-shacl",
        "ArtDefEnv": "def:env", "ArtDefSlice": "def:slice", "ArtSecCandidates": "sec:candidates",
        "ArtSecObligations": "sec:obligations", "ArtSecPolicy": "sec:policy", "ArtSecTrace": "sec:trace",
        "ArtSecSelection": "sec:selection", "ArtLstAnnotation": "lst:r001"}
lines = [f"\\newcommand{{\\{k}}}{{{num(v)}}}" for k, v in want.items()]
(OUT / "article_refs.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("article_refs.tex:", "; ".join(lines))
