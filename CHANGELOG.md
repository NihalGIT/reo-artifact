# Changelog

## REO v1.0.0 — release presented and evaluated in the article

- Ontology: core module (`reo-core.ttl`, 500 triples; 27 classes; seven views, auxiliary classes, OWL axioms,
  validator binding, identity links) and derivation module (`reo-derivation.ttl`, 253 triples; requirements,
  decision states, PROV-O-aligned decision records); version IRIs
  `http://re-environment-onto.org/ontology/release/1.0.0` and
  `http://re-environment-onto.org/ontology/derivation/release/1.0.0`.
- SHACL: `reo-shapes.ttl` and `reo-derivation-shapes.ttl` (495 triples; 14 node shapes, 62 property shapes,
  9 SPARQL-based constraints).
- Contract: reference checker `reo_checker.py` and SPARQL implementation `scripts/sparql_oracle.py`, policy
  `ecre-policy-1.0.0` (obligations O1–O9, six decision states, read-set traces, PROV-O records, trace-based
  reassessment with the vocabulary rule, the premise-removal rule, and one-step identity migration).
- Evaluation: `scripts/run_all.py` (25 steps) regenerates every result reported in the article; see
  `EXPERIMENTS.md`.
- Package documentation (2026-11-09): the submission package contains only the material needed to reproduce the
  evaluation of REO v1.0.0; earlier development states are kept in the authors' research archive, not in this
  package, and the evaluation scripts no longer run them. Apart from the descriptive comment of the core ontology,
  no code of the contract, vocabulary term, shape, data, or reported result changed.
