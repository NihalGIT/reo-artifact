# REO v1.0.0 — RE-Environment Ontology and EC-RE reference checker

Replication package of *Environment Engineering for Requirements Engineering: The REO Ontology
and an Executable Derivation Contract* (IEEE Access). REO v1.0.0 is the artifact presented and
evaluated in that article.

REO models the socio-technical environment of a system as seven typed views (actors, goals, domain
concepts, constraints, contexts, uncertainties, interactions) connected by typed relations. The
derivation contract assesses a candidate requirement through its annotation, a manually written mapping
of the requirement to environment elements. It returns one of six decision states (accepted, accepted
with reservations, conditional, pending review, rejected, not derived) with justifications, and records
the elements it read (the trace). Decision records are serialised with PROV-O. The contract assesses the
annotation, not the sentence: it does not check that the two agree, and no candidate generator is included.

## Layout

| Path | Content |
|---|---|
| `ontology/reo-core.ttl` | Seven views, auxiliary classes, OWL axioms (restrictions, disjointness), validator binding, identity links |
| `ontology/reo-derivation.ttl` | Requirements, decision states, PROV-O-aligned decision records |
| `ontology/reo-shapes.ttl`, `ontology/reo-derivation-shapes.ttl` | SHACL shapes (14 node shapes, 62 property shapes, 9 SPARQL-based constraints) |
| `examples/medical-emergency/` | Synthetic emergency-prescription environment (`hospital.ttl`), extension, ten requirements |
| `test-cases/` | Three SHACL fixtures (conforming, warning, violations) |
| `reo_checker.py` | Reference implementation of the contract (policy `ecre-policy-1.0.0`), change impact, reassessment with identity migration and the premise-removal rule, PROV-O records (written and read back) |
| `prototype.py` | Regression runner: 18 decision scenarios, 4 impact queries, 20 targeted cases (RDF mutations + SHACL); writes `results/regression_results.json` and `evaluation_results.csv` |
| `scripts/sparql_oracle.py` | Second implementation of the contract in SPARQL, sharing no code with the checker (differential testing) |
| `scripts/run_evaluation.py` | Schema-derived mutation corpus, differential testing, schema-only decision rules, selection rules, element-level trace and impact-selection measures |
| `scripts/check_corrections.py` | 68 corner cases with expectations stated from the contract's semantics |
| `scripts/property_checks.py`, `scripts/property_check_v4.py` | Implementation-property checks V1–V4 with random generators that share no code with the corpus operators |
| `scripts/history_rule_corpus.py` | Effect of the premise-removal rule of reassessment on the mutation corpus |
| `scripts/annotation_perturbation.py` | Sensitivity of decisions to single-field errors in the annotation |
| `scripts/view_relevance.py` | Views of the behaviour-derived relevant requirement-element pairs |
| `scripts/ir_sensitivity.py` | Threshold grid of text-similarity (TF-IDF) trace selection |
| `scripts/determinism_check.py` | Decisions, records, a corpus sample and E1 under several `PYTHONHASHSEED` values |
| `scripts/measure_timing.py` | Repeated timing of projection and assessment, with the execution environment |
| `scripts/run_manifest.py` | Versions, seeds, commands and file digests of a run (`results/run_manifest.json`) |
| `scripts/compare_results.py` | Compares two result folders (run times, environment data and digests excluded) |
| `scripts/` (other) | Reasoner, SHACL, competency questions, pitfall scan, migration, metrics, figure, tables |
| `evaluation/competency-questions/`, `evaluation/view-derivation/`, `evaluation/extension/` | Competency questions, view coding (matrix, codebook, script), vocabulary extension |
| `evaluation/llm-evaluation/` | Studies E1–E3 with language-model evaluators: frozen packs, prompts, outputs, tool-log audit, scoring |
| `evaluation/independent-evaluation/`, `evaluation/human-study/` | Protocols for external evaluators and an analyst pilot (not executed) |
| `EXPERIMENTS.md` | Registry of every check and experiment: question, procedure, reference, metrics, biases, command, seed, outputs |
| `results/` | Recorded outputs used in the article, with `run_all.log` and `run_manifest.json` of the run |
| `generated/` | Tables and macros generated from `results/`; vocabulary inventory |
| `supplementary/` | Supplementary material of the article (Fig. S1, Tables S1–S12) and its sources |

## Reproduce

```bash
python -m pip install -r requirements.txt   # Python 3.13; Java 21 for HermiT; Graphviz for the figure
python scripts/run_all.py                   # 25 steps, about 20-30 min on two cores (mutation corpus included)
# expected last line: "All steps passed." Run times (results/timing.json) depend on the machine.
# results/run_manifest.json records versions, seeds, commands and file digests of the run.
python scripts/run_all.py --skip-corpus     # shorter run that reuses results/evaluation_summary.json
python scripts/compare_results.py COMMITTED_RESULTS results   # compare a fresh run with a saved copy of results/
```

Single steps: `python prototype.py`, `python scripts/run_competency_questions.py`,
`python scripts/reasoner_check.py`, `python scripts/run_shacl.py`, `python scripts/run_evaluation.py`.

Standalone SHACL check of the example (the ontology, which holds the OWL axioms, is given as an extra graph
so that RDFS inference knows, for instance, that a system actor is an actor; warnings do not make the graph
non-conforming):

```bash
python -m pyshacl -s ontology/reo-shapes.ttl -e ontology/reo-core.ttl -i rdfs --allow-warnings \
  -f human examples/medical-emergency/hospital.ttl
```

## Scope

Implemented and evaluated: the ontology modules, SHACL shapes, the decision contract over manually
annotated requirements (ill-formed annotations go to review; references are resolved by IRI), read-set
traces, PROV-O records (generated, SHACL-validated, queried, and read back as the previous assessment; no
persistent store), trace-based reassessment (every requirement is reassessed after a vocabulary change),
identity migration with `reo:supersededBy` (one supersession step; an ambiguous or invalid successor goes
to review), and the premise-removal rule of reassessment. The transitive dependency closure exists only as
an alternative selection rule evaluated for comparison (`run_evaluation.py`, M4). Not implemented:
selection of the relevant environment (S0), candidate generation (Gen), extraction of annotations from
natural language, multi-source fusion and entity resolution, runtime synchronisation.

Analyst effort has not been measured. The studies in `evaluation/llm-evaluation/` used language-model
instances as evaluators under a blinding and freezing protocol; they are a proxy, not a human study.
The medical example is a synthetic fixture. Identifiers such as `C001_HIPAA` and `C002_FDA` are
illustrative labels, not interpretations of the regulations.

## Identifiers

Ontology version IRIs: `http://re-environment-onto.org/ontology/release/1.0.0` (core) and
`http://re-environment-onto.org/ontology/derivation/release/1.0.0` (derivation module). Contract policy
written into every decision record: `ecre-policy-1.0.0`. This package has not yet been deposited; it will
receive its own Zenodo version DOI. The Zenodo record 10.5281/zenodo.23420533 archives an earlier state of
the public repository and is not the artifact evaluated in the article.

## Licence

MIT (see `LICENSE`). Please cite the article (see `CITATION.cff`).
