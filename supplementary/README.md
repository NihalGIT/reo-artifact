# Supplementary material of the article

`REO_supplementary_material.pdf` holds Fig. S1 and Tables S1–S12 cited by the article: the derivation module, the
37 object and 47 datatype properties of REO v1.0.0, the detailed tables of the evaluation (studies E1 and E2, trace
accuracy with the full threshold grid of text-similarity trace selection, identity migration), the coverage of the
views by alternative metamodels, an evidence summary per research question, the version of every component of
this package (Table S9), the mutation operators of the corpus (Table S10), the terms and notation of the article
(Table S11), and the confusion matrices of schema-only validation versus contract decisions (Table S12).

Rebuild (pdfLaTeX): `python make_inputs.py` (copies `generated/*.tex` and `figures/reo-derivation.pdf`, writes
Tables S5 and S10 (total row) from `results/*.json`, Table S12 from `generated/tab_shacl_confusion.tex`, and
Table S9 from the files of the package), then `pdflatex supplement.tex` twice. `inputs/article_refs.tex` holds the
section and table numbers of the compiled article; inside this package it is kept as stored.
