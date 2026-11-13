#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
REO reference checker, REO v1.0.0
====================================

Deterministic reference implementation of the EC-RE derivation contract.

The checker evaluates a *requirement* (a candidate statement together with its
explicit mapping to environment elements, see ``reo-derivation.ttl``) against
an RDF environment snapshot typed with REO, and returns

    (decision state, justifications, trace)

The trace is the *read set*: every environment identifier whose description
was consulted to reach the decision. Read-set recording is what makes direct
change-impact analysis sound (see ``touched_identifiers`` and the paper's
Proposition 2): the decision is a function of the requirement annotation, the
policy, and the triples whose subject or object belongs to the trace.

Policy (version ``POLICY_VERSION``) — decision order:

    pending_review   the annotation is ill-formed: a field that takes one value has
                     several, or a value is not of the kind its property expects
                     (a literal where an element is expected, a non-boolean flag);
                     the contract does not choose between values (empty trace)
    not_derived      seed goal or context undefined
    pending_review   the annotation references no environment element (refs(q) empty)
    rejected         a checked hard obligation failed, or a premise blocking
                     the seed lineage was refuted (status "rejected")
    pending_review   a hard obligation could not be checked (undefined rule,
                     no registered validator, conflicting values or identifiers,
                     invalidated affected evidence, uninterpretable uncertainty
                     status, an annotated action without a responsible actor,
                     an expected source or protocol without a used interaction)
    conditional      an unvalidated uncertainty blocks the seed lineage, or an
                     unvalidated high/critical uncertainty affects consulted
                     evidence
    accepted_with_reservations
                     a soft obligation failed or is unchecked, or a
                     low/medium unvalidated uncertainty affects evidence
    accepted         none of the above

No language model is invoked. The natural-language text is not parsed.
Annotation references are resolved by IRI, field by field: an IRI that is not the IRI
of a model element denotes no element (it is undefined), even when an element of the
model has the same local name. Requirement identifiers must be unique.

Re-assessment (``reassess``) adds identity migration and one history rule: a premise
that was not established in the previous assessment and no longer exists in the new
snapshot is not treated as resolved. It gives an unchecked outcome at the level of its
previous outcome: a hard one sends the requirement to review, a soft one (an unvalidated
premise of low or medium impact) leaves a reservation. The premise stays unresolved in
the new assessment and in its record (reo:unresolvedHardPremise or
reo:unresolvedSoftPremise), so the rule also applies after later changes and to a
previous assessment read back from its record (``previous_from_records``). Migration
to a successor that is missing or typed in another view goes to review.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

import rdflib
from rdflib import OWL, RDF, RDFS, Namespace, URIRef, Literal

REO = Namespace("http://re-environment-onto.org/ontology#")
PROV = Namespace("http://www.w3.org/ns/prov#")
EX = Namespace("http://hospital.example.org#")
REQ = Namespace("http://hospital.example.org/requirements#")

POLICY_VERSION = "ecre-policy-1.0.0"
ROOT = Path(__file__).resolve().parent
ONTOLOGY_FILES = [ROOT / "ontology" / "reo-core.ttl", ROOT / "ontology" / "reo-derivation.ttl"]

ACCEPTED = "accepted"
ACCEPTED_WITH_RESERVATIONS = "accepted_with_reservations"
CONDITIONAL = "conditional"
PENDING_REVIEW = "pending_review"
REJECTED = "rejected"
NOT_DERIVED = "not_derived"
STATES = [ACCEPTED, ACCEPTED_WITH_RESERVATIONS, CONDITIONAL, PENDING_REVIEW, REJECTED, NOT_DERIVED]
STATE_IRI = {
    ACCEPTED: REO.Accepted, ACCEPTED_WITH_RESERVATIONS: REO.AcceptedWithReservations,
    CONDITIONAL: REO.Conditional, PENDING_REVIEW: REO.PendingReview,
    REJECTED: REO.Rejected, NOT_DERIVED: REO.NotDerived,
}

PASS, FAIL, UNCHECKED, NOT_TRIGGERED = "pass", "fail", "unchecked", "not_triggered"

# View roots. Membership is computed with the rdfs:subClassOf closure of the
# loaded ontology, so project-specific subclasses (extensions) are recognised.
VIEW_ROOTS = {
    "actors": REO.Actor, "goals": REO.Goal, "domain": REO.DomainConcept,
    "constraints": REO.Constraint, "contexts": REO.Context,
    "uncertainties": REO.Uncertainty, "interactions": REO.Interaction,
    "actions": REO.Action, "properties": REO.DomainProperty, "invariants": REO.DomainInvariant,
}
SEVEN_VIEWS = ["actors", "goals", "domain", "constraints", "contexts", "uncertainties", "interactions"]

# Attributes that must be single-valued for the checker to interpret them.
FUNCTIONAL = {
    "actors": ["authority"], "constraints": ["enforcement", "validatorKey", "formalExpression"],
    "uncertainties": ["status", "impact"], "interactions": ["source", "target", "protocol"],
    "goals": [], "domain": [], "contexts": [], "actions": ["label"], "properties": [], "invariants": [],
}


def local(term) -> str:
    s = str(term)
    return s.split("#")[-1] if "#" in s else s.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Environment projection
# ---------------------------------------------------------------------------
@dataclass
class Environment:
    views: Dict[str, Dict[str, dict]] = field(default_factory=lambda: {v: {} for v in VIEW_ROOTS})
    conflicts: Dict[str, List[str]] = field(default_factory=dict)   # id -> conflicting attributes
    multi_typed: Dict[str, List[str]] = field(default_factory=dict)  # id -> several views
    collisions: Dict[str, List[str]] = field(default_factory=dict)   # local name -> several IRIs
    # Applicability and premise links that start at an element which is not typed as a constraint
    # (appliesTo) or as an uncertainty (blocks, affects): predicate -> target -> subjects. The contract
    # reads these subjects (their type) without treating them as rules or premises.
    foreign: Dict[str, Dict[str, List[str]]] = field(default_factory=dict)
    # IRIs: of the typed elements (local name -> IRIs; used to resolve annotation references), and of every
    # IRI that occurs in the environment graph (used to write the IRIs of consulted elements in records).
    iris: Dict[str, List[str]] = field(default_factory=dict)
    all_iris: Dict[str, List[str]] = field(default_factory=dict)
    snapshot: str = ""

    def __getattr__(self, name):  # env.actors, env.goals, ...
        if name in VIEW_ROOTS:
            return self.__dict__["views"][name]
        raise AttributeError(name)

    def view_of(self, eid: str) -> Optional[str]:
        for v, d in self.views.items():
            if eid in d:
                return v
        return None

    def exists(self, eid: str) -> bool:
        return self.view_of(eid) is not None

    def all_ids(self) -> Set[str]:
        out: Set[str] = set()
        for d in self.views.values():
            out |= set(d)
        return out


_ONTO_CACHE: Dict[str, rdflib.Graph] = {}


def ontology_graph() -> rdflib.Graph:
    if "g" not in _ONTO_CACHE:
        g = rdflib.Graph()
        for f in ONTOLOGY_FILES:
            g.parse(f, format="turtle")
        _ONTO_CACHE["g"] = g
    return _ONTO_CACHE["g"]


def _view_of_class(onto: rdflib.Graph, data: rdflib.Graph) -> Dict[URIRef, str]:
    """Map every class (ontology or data-declared subclass) to its view."""
    sub: Dict[URIRef, Set[URIRef]] = {}
    for g in (onto, data):
        for s, o in g.subject_objects(RDFS.subClassOf):
            if isinstance(s, URIRef) and isinstance(o, URIRef):
                sub.setdefault(s, set()).add(o)
    roots = {r: v for v, r in VIEW_ROOTS.items()}
    memo: Dict[URIRef, Optional[str]] = {}

    def find(c, seen=()):
        if c in roots:
            return roots[c]
        if c in memo:
            return memo[c]
        memo[c] = None
        for p in sorted(sub.get(c, ()), key=str):  # deterministic order
            if p not in seen:
                v = find(p, seen + (c,))
                if v:
                    memo[c] = v
                    break
        return memo[c]

    classes = set(sub) | set(roots)
    for g in (onto, data):
        classes |= {o for o in g.objects(None, RDF.type) if isinstance(o, URIRef)}
    return {c: find(c) for c in classes if find(c)}


# Requirement annotations and decision records may share a dataset (or a graph) with
# the environment, but they are not part of the environment snapshot: the snapshot
# hash and the touched-identifier diff ignore them (Sections VI-B and VI-D).
DERIVATION_TYPES = {REO.Requirement, REO.DerivationRecord, REO.ValidationActivity}
DERIVATION_PREDICATES = {REO[n] for n in (
    "assesses", "hasDecision", "environmentSnapshot", "candidateSnapshot", "policyVersion", "hasJustification",
    "consulted", "unresolvedPremise", "unresolvedHardPremise", "unresolvedSoftPremise",
    "supersedes", "requirementId", "requirementText", "hasSeedGoal", "assessedInContext", "hasResponsibleActor",
    "performsAction", "requiresAuthorityOf", "refersToConcept", "refersToProperty", "usesInteraction",
    "expectsInteractionSource", "requiresProtocol", "declaresHardConstraint", "declaresSoftConstraint")} | {
    PROV.wasGeneratedBy, PROV.startedAtTime, PROV.endedAtTime, PROV.used}


def environment_triples(g: rdflib.Graph) -> Set[Tuple]:
    """The environment snapshot of a graph: every triple without blank nodes, except
    requirement annotations and decision records (subjects typed as Requirement,
    DerivationRecord or ValidationActivity, and derivation-module predicates)."""
    excluded = {s for t in DERIVATION_TYPES for s in g.subjects(RDF.type, t)}
    return {(s, p, o) for s, p, o in g
            if not isinstance(s, rdflib.BNode) and not isinstance(o, rdflib.BNode)
            and s not in excluded and p not in DERIVATION_PREDICATES}


def snapshot_hash(g: rdflib.Graph, triples: Optional[Set[Tuple]] = None) -> str:
    """SHA-256 over the sorted N-Triples lines of the environment snapshot (`triples`, if given, must be
    environment_triples(g))."""
    lines = sorted(f"{s.n3()} {p.n3()} {o.n3()} ." for s, p, o in (environment_triples(g) if triples is None
                                                                   else triples))
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def load_graph(paths: Iterable) -> rdflib.Graph:
    g = rdflib.Graph()
    for p in paths:
        g.parse(str(p), format="turtle")
    return g


def project(g: rdflib.Graph) -> Environment:
    """Project an RDF environment graph into the seven views (+ auxiliary)."""
    env = Environment()
    vmap = _view_of_class(ontology_graph(), g)

    def vals(s, p) -> List[str]:
        out = []
        for o in g.objects(s, p):
            out.append(local(o) if isinstance(o, URIRef) else str(o))
        return sorted(set(out))

    owners: Dict[str, List[str]] = {}
    eid_views: Dict[str, Set[str]] = {}
    # Subjects are processed in IRI order. Two IRIs with the same local name (an identifier
    # collision) are merged by union, so no value is discarded, and reported in env.collisions;
    # obligation O9 then leaves every check on that identifier undecided.
    for s in sorted((x for x in set(g.subjects(RDF.type, None)) if isinstance(x, URIRef)), key=str):
        views = sorted({vmap[t] for t in g.objects(s, RDF.type) if t in vmap})
        if not views:
            continue
        def _store(view):
            if view == "actors":
                d = {"authority": vals(s, REO.authorityLevel), "capabilities": vals(s, REO.hasCapability),
                     "responsibleFor": vals(s, REO.isResponsibleFor), "subjectTo": vals(s, REO.isSubjectTo),
                     "name": vals(s, REO.actorName)}
            elif view == "goals":
                d = {"refines": vals(s, REO.refines), "stakeholders": vals(s, REO.hasStakeholder),
                     "priority": vals(s, REO.priority), "name": vals(s, REO.goalName),
                     "strategic": REO.StrategicGoal in set(g.objects(s, RDF.type))}
            elif view == "domain":
                d = {"properties": vals(s, REO.hasProperty), "invariants": vals(s, REO.hasInvariant),
                     "relatedTo": vals(s, REO.relatedTo), "name": vals(s, REO.conceptName)}
            elif view == "constraints":
                d = {"enforcement": vals(s, REO.enforcementLevel), "validatorKey": vals(s, REO.validatorKey),
                     "formalExpression": vals(s, REO.formalExpression), "appliesTo": vals(s, REO.appliesTo),
                     "exceptions": vals(s, REO.hasException), "name": vals(s, REO.constraintName),
                     "naturalLanguage": vals(s, REO.naturalLanguage)}
            elif view == "contexts":
                d = {"activeConstraints": vals(s, REO.hasActiveConstraint), "activeActors": vals(s, REO.hasActiveActor),
                     "properties": vals(s, REO.contextProperty), "name": vals(s, REO.contextName)}
            elif view == "uncertainties":
                d = {"status": vals(s, REO.status), "impact": vals(s, REO.impactLevel),
                     "affects": vals(s, REO.affects), "blocks": vals(s, REO.blocks),
                     "name": vals(s, REO.uncertaintyName), "subject": vals(s, REO.subject)}
            elif view == "interactions":
                d = {"source": vals(s, REO.hasSource), "target": vals(s, REO.hasTarget),
                     "data": vals(s, REO.exchangesData), "protocol": vals(s, REO.protocol),
                     "governedBy": vals(s, REO.governedBy), "name": vals(s, REO.interactionName)}
            elif view == "actions":
                d = {"label": vals(s, RDFS.label)}
            elif view == "properties":
                d = {"name": vals(s, REO.propertyName)}
            else:
                d = {"expression": vals(s, REO.invariantExpression)}
            prev = env.views[view].get(eid)
            if prev is not None:  # identifier collision: union of the values of all IRIs
                for k, v in d.items():
                    if isinstance(v, list):
                        d[k] = sorted(set(prev.get(k, [])) | set(v))
                    elif isinstance(v, bool):
                        d[k] = bool(prev.get(k)) or v
            env.views[view][eid] = d
            bad = [a for a in FUNCTIONAL.get(view, []) if len(d.get(a, [])) > 1]
            if bad:
                env.conflicts[eid] = sorted(set(env.conflicts.get(eid, [])) | set(bad))
        eid = local(s)
        owners.setdefault(eid, []).append(str(s))
        eid_views.setdefault(eid, set()).update(views)
        for view in views:
            _store(view)
        continue
    env.multi_typed = {e: sorted(v) for e, v in eid_views.items() if len(v) > 1}
    env.collisions = {e: sorted(set(i)) for e, i in owners.items() if len(set(i)) > 1}
    # Links followed backwards by the contract (applicability of rules, premises) whose subject is not
    # typed as a constraint or an uncertainty. A later change of that subject's type changes how the
    # contract interprets the link, so the subject must be in the read set (Proposition 2).
    foreign: Dict[str, Dict[str, Set[str]]] = {}
    for pname, view in (("appliesTo", "constraints"), ("blocks", "uncertainties"), ("affects", "uncertainties")):
        for s, o in g.subject_objects(REO[pname]):
            if isinstance(s, URIRef) and isinstance(o, URIRef) and local(s) not in env.views[view]:
                foreign.setdefault(pname, {}).setdefault(local(o), set()).add(local(s))
    env.foreign = {p: {t: sorted(v) for t, v in sorted(d.items())} for p, d in sorted(foreign.items())}
    env.iris = {e: sorted(set(i)) for e, i in sorted(owners.items())}
    vocab = (str(REO), str(RDF), str(RDFS), str(OWL), str(PROV), "http://www.w3.org/ns/shacl#",
             "http://www.w3.org/2001/XMLSchema#")
    alli: Dict[str, Set[str]] = {}
    et = environment_triples(g)
    for s, p, o in et:
        for t in ((s, o) if p != RDF.type else (s,)):
            if isinstance(t, URIRef) and not str(t).startswith(vocab):
                alli.setdefault(local(t), set()).add(str(t))
    env.all_iris = {e: sorted(i) for e, i in sorted(alli.items())}
    env.snapshot = snapshot_hash(g, et)
    return env


def one(values: List[str]) -> Optional[str]:
    return values[0] if values else None


# ---------------------------------------------------------------------------
# Requirement (candidate + explicit mapping)
# ---------------------------------------------------------------------------
@dataclass
class Requirement:
    rid: str
    text: str
    seed_goal: str
    context: str
    actor: Optional[str] = None
    actions: List[str] = field(default_factory=list)
    authority_of: Optional[str] = None
    concepts: List[str] = field(default_factory=list)
    properties: List[str] = field(default_factory=list)
    interaction: Optional[str] = None
    interaction_source: Optional[str] = None
    requires_protocol: bool = False
    hard: List[str] = field(default_factory=list)
    soft: List[str] = field(default_factory=list)
    iri: Optional[str] = None                                         # the reo:Requirement resource
    ref_iris: Dict[str, List[str]] = field(default_factory=dict)      # local name -> IRIs used by the annotation
    field_iris: Dict[str, List[str]] = field(default_factory=dict)    # attribute -> IRIs written in that field
    conflicts: Dict[str, List[str]] = field(default_factory=dict)     # single-valued field -> several values
    illformed: Dict[str, List[str]] = field(default_factory=dict)     # field -> values of the wrong kind

    def references(self) -> Set[str]:
        refs = {x for x in (self.actor, self.authority_of, self.interaction, self.interaction_source) if x}
        return refs | set(self.actions) | set(self.concepts) | set(self.properties)


# Annotation fields: predicate, kind of value, and whether the field takes one value (reo-derivation-shapes.ttl
# gives the single-valued fields sh:maxCount 1). Kinds: "iri" (an element of the model), "literal", "boolean"
# (a well-typed xsd:boolean literal).
ANNOTATION_FIELDS = {
    "requirementId": (REO.requirementId, "literal", True),
    "requirementText": (REO.requirementText, "literal", True),
    "hasSeedGoal": (REO.hasSeedGoal, "iri", True),
    "assessedInContext": (REO.assessedInContext, "iri", True),
    "hasResponsibleActor": (REO.hasResponsibleActor, "iri", True),
    "requiresAuthorityOf": (REO.requiresAuthorityOf, "iri", True),
    "usesInteraction": (REO.usesInteraction, "iri", True),
    "expectsInteractionSource": (REO.expectsInteractionSource, "iri", True),
    "requiresProtocol": (REO.requiresProtocol, "boolean", True),
    "performsAction": (REO.performsAction, "iri", False),
    "refersToConcept": (REO.refersToConcept, "iri", False),
    "refersToProperty": (REO.refersToProperty, "iri", False),
    "declaresHardConstraint": (REO.declaresHardConstraint, "iri", False),
    "declaresSoftConstraint": (REO.declaresSoftConstraint, "iri", False),
}
FIELD_ATTR = {"hasSeedGoal": "seed_goal", "assessedInContext": "context", "hasResponsibleActor": "actor",
              "requiresAuthorityOf": "authority_of", "usesInteraction": "interaction",
              "expectsInteractionSource": "interaction_source", "performsAction": "actions",
              "refersToConcept": "concepts", "refersToProperty": "properties", "declaresHardConstraint": "hard",
              "declaresSoftConstraint": "soft"}
SINGLE_VALUED = {n: p for n, (p, _, single) in ANNOTATION_FIELDS.items() if single}
REFERENCE_PREDICATES = [p for p, kind, _ in ANNOTATION_FIELDS.values() if kind == "iri"]
_KIND_TEXT = {"iri": "an IRI", "literal": "a literal", "boolean": "an xsd:boolean literal"}


def _well_kinded(v, kind: str) -> bool:
    if kind == "iri":
        return isinstance(v, URIRef)
    if kind == "literal":
        return isinstance(v, Literal)
    return (isinstance(v, Literal) and v.datatype == rdflib.XSD.boolean
            and not getattr(v, "ill_typed", False))


def load_requirements(g: rdflib.Graph) -> List[Requirement]:
    """Read the reo:Requirement annotations of a graph. Requirement identifiers must be unique (a duplicate
    raises ValueError). A single-valued field with several values, and a field whose value is not of the kind
    its property expects (a literal where an element is expected, an ill-typed boolean), make the annotation
    ill-formed: the values are kept, and the contract does not choose between them or interpret them (see
    annotation_problems and validate)."""
    out = []
    for r in sorted(set(g.subjects(RDF.type, REO.Requirement))):
        def o1(p):
            v = next(iter(sorted(g.objects(r, p))), None)
            return None if v is None else (local(v) if isinstance(v, URIRef) else str(v))

        def om(p):
            return sorted(local(v) for v in g.objects(r, p))
        conflicts, illformed = {}, {}
        for name, (p, kind, single) in ANNOTATION_FIELDS.items():
            values = set(g.objects(r, p))
            if single and len(values) > 1:
                conflicts[name] = sorted(v.n3() for v in values)
            bad = sorted(v.n3() for v in values if not _well_kinded(v, kind))
            if bad:
                illformed[name] = bad
        ref_iris: Dict[str, Set[str]] = {}
        field_iris: Dict[str, List[str]] = {}
        for name, attr in FIELD_ATTR.items():
            iris = sorted(str(v) for v in g.objects(r, ANNOTATION_FIELDS[name][0]) if isinstance(v, URIRef))
            if iris:
                field_iris[attr] = iris
            for i in iris:
                ref_iris.setdefault(local(i), set()).add(i)
        protocol = any(_well_kinded(v, "boolean") and v.toPython() is True for v in g.objects(r, REO.requiresProtocol))
        out.append(Requirement(
            rid=o1(REO.requirementId) or local(r), text=o1(REO.requirementText) or "",
            seed_goal=o1(REO.hasSeedGoal), context=o1(REO.assessedInContext),
            actor=o1(REO.hasResponsibleActor), actions=om(REO.performsAction),
            authority_of=o1(REO.requiresAuthorityOf), concepts=om(REO.refersToConcept),
            properties=om(REO.refersToProperty), interaction=o1(REO.usesInteraction),
            interaction_source=o1(REO.expectsInteractionSource), requires_protocol=protocol,
            hard=om(REO.declaresHardConstraint), soft=om(REO.declaresSoftConstraint),
            iri=str(r), ref_iris={k: sorted(v) for k, v in sorted(ref_iris.items())}, field_iris=field_iris,
            conflicts=conflicts, illformed=illformed))
    seen: Dict[str, List[str]] = {}
    for q in out:
        seen.setdefault(q.rid, []).append(q.iri)
    dups = {k: v for k, v in seen.items() if len(v) > 1}
    if dups:
        raise ValueError(f"duplicate requirement identifiers {dups}: each reo:Requirement needs its own "
                         "reo:requirementId")
    return out


def annotation_problems(q: Requirement) -> List[str]:
    """Well-formedness of the annotation alpha(q): the fields that have several values although they take one,
    and the values that are not of the expected kind. The contract interprets neither."""
    out = [f"the annotation gives several values to {f}: {', '.join(v)}" for f, v in sorted(q.conflicts.items())]
    out += [f"the annotation gives {f} a value that is not {_KIND_TEXT[ANNOTATION_FIELDS[f][1]]}: {', '.join(v)}"
            for f, v in sorted(q.illformed.items())]
    return out


# ---------------------------------------------------------------------------
# Validators (bound to constraints through reo:validatorKey)
# ---------------------------------------------------------------------------
@dataclass
class Assessment:
    env: Environment
    req: Requirement
    reads: Set[str] = field(default_factory=set)
    type_reads: Set[str] = field(default_factory=set)  # elements whose type only was consulted
    log: List[Tuple[str, str, str]] = field(default_factory=list)  # (level, outcome, message)

    def read(self, *ids):
        for i in ids:
            if i:
                self.reads.add(i)

    def read_type(self, *ids):
        """Record elements whose type was consulted to decide that they are not rules or premises."""
        for i in ids:
            if i:
                self.type_reads.add(i)

    def trace(self) -> Set[str]:
        return set(self.reads) | set(self.type_reads)

    def record(self, level: str, outcome: str, msg: str):
        self.log.append((level, outcome, msg))


_RULE = re.compile(r"^\s*([A-Za-z_][\w]*)\s*(?:\(.*?\))?\s*→\s*(.+)$")


def _antecedent(expr: Optional[str]) -> Optional[str]:
    m = _RULE.match(expr or "")
    return m.group(1) if m else None


def _triggered(a: Assessment, antecedent: str) -> Optional[bool]:
    """True/False, or None when an action label is conflicting (undecidable)."""
    hit = False
    for act in a.req.actions:
        a.read(act)
        labels = a.env.actions.get(act, {}).get("label", [])
        if len(labels) > 1:
            return None
        hit |= one(labels) == antecedent
    return hit


def v_authority_equals(a: Assessment, cid: str, c: dict) -> Tuple[str, str]:
    expr = one(c["formalExpression"])
    ant = _antecedent(expr)
    m = re.search(r"actor\.authority\s*=\s*([\w-]+)", expr or "")
    if not ant or not m:
        return UNCHECKED, f"{cid}: formal expression not interpretable by authority-equals"
    trig = _triggered(a, ant)
    if trig is None:
        return UNCHECKED, f"{cid}: conflicting action labels; antecedent '{ant}' undecidable"
    if not trig:
        return NOT_TRIGGERED, f"{cid}: antecedent '{ant}' not performed by the requirement"
    who = a.req.authority_of or a.req.actor
    a.read(who)
    actor = a.env.actors.get(who)
    if actor is None:
        return FAIL, f"{cid}: actor {who} undefined"
    if len(actor["authority"]) > 1:
        return UNCHECKED, f"{cid}: conflicting authority values for {who}"
    if one(actor["authority"]) != m.group(1):
        return FAIL, f"{cid}: {who} authority {one(actor['authority'])!r} differs from required {m.group(1)!r}"
    return PASS, f"{cid}: {who} has authority {m.group(1)}"


def v_consent_or_exception(a: Assessment, cid: str, c: dict) -> Tuple[str, str]:
    ant = _antecedent(one(c["formalExpression"]))
    if not ant:
        return UNCHECKED, f"{cid}: formal expression not interpretable by consent-or-exception"
    trig = _triggered(a, ant)
    if trig is None:
        return UNCHECKED, f"{cid}: conflicting action labels; antecedent '{ant}' undecidable"
    if not trig:
        return NOT_TRIGGERED, f"{cid}: antecedent '{ant}' not performed by the requirement"
    ctx = a.req.context
    a.read(ctx)
    if ctx in c["exceptions"]:
        return PASS, f"{cid}: {ctx} is a declared exception"
    if "consent_obtained=true" in a.env.contexts.get(ctx, {}).get("properties", []):
        return PASS, f"{cid}: consent is asserted in {ctx}"
    return FAIL, f"{cid}: consent required in {ctx}, which is not an exception and asserts no consent"


VALIDATORS: Dict[str, Callable[[Assessment, str, dict], Tuple[str, str]]] = {
    "authority-equals": v_authority_equals,
    "consent-or-exception": v_consent_or_exception,
}


# ---------------------------------------------------------------------------
# Decision procedure
# ---------------------------------------------------------------------------
@dataclass
class Result:
    rid: str
    state: str
    justifications: List[str]
    trace: Set[str]
    obligations: List[Tuple[str, str, str]]
    snapshot: str = ""
    premises: Dict[str, str] = field(default_factory=dict)  # uncertainty id -> O8 outcome
    candidate: str = ""                                     # candidate snapshot (q and its annotation)
    iris: Dict[str, List[str]] = field(default_factory=dict)  # trace identifier -> IRIs (for the records)
    req_iri: Optional[str] = None                           # IRI of the assessed reo:Requirement
    records: Tuple[str, ...] = ()                           # IRIs of the stored record(s) it was read back from

    def key(self):
        return (self.state, tuple(sorted(self.justifications)), tuple(sorted(self.trace)))


def seed_lineage(a: Assessment) -> List[str]:
    out, frontier = [], [a.req.seed_goal]
    while frontier:
        g = frontier.pop(0)
        if g in out:
            continue
        a.read(g)
        if g not in a.env.goals:
            continue
        out.append(g)
        frontier.extend(sorted(a.env.goals[g]["refines"]))
    return out


def applicable_constraints(a: Assessment, lineage: List[str]) -> Dict[str, str]:
    """Return {constraint id: route}. Only the requirement's own references are
    used (not the whole slice), so that every element whose links determine
    applicability is in the read set."""
    env, q = a.env, a.req
    routes: Dict[str, str] = {}
    for cid in q.hard:
        routes.setdefault(cid, "declared-hard")
    for cid in q.soft:
        routes.setdefault(cid, "declared-soft")
    ctx = env.contexts.get(q.context, {})
    for cid in ctx.get("activeConstraints", []):
        routes.setdefault(cid, "context-active")
    for who in {q.actor, q.authority_of} - {None}:
        a.read(who)
        for cid in env.actors.get(who, {}).get("subjectTo", []):
            routes.setdefault(cid, f"isSubjectTo({who})")
    if q.interaction:
        a.read(q.interaction)
        for cid in env.interactions.get(q.interaction, {}).get("governedBy", []):
            routes.setdefault(cid, f"governedBy({q.interaction})")
    scope = q.references() | set(lineage)
    # Incoming appliesTo links of every scope element are consulted.
    a.read(*sorted(scope))
    for cid, c in sorted(env.constraints.items()):
        hit = sorted(set(c["appliesTo"]) & scope)
        if hit:
            routes.setdefault(cid, f"appliesTo({hit[0]})")
    # An appliesTo link from an element that is not typed as a constraint is consulted too: its subject
    # is not a rule, but its type was read to decide so (it enters the trace, not R(q)).
    for e in sorted(scope):
        a.read_type(*env.foreign.get("appliesTo", {}).get(e, []))
    return routes


def resolve_references(env: Environment, q: Requirement) -> Tuple[Requirement, Dict[str, List[str]]]:
    """Annotation references are resolved by IRI, field by field. A reference whose IRI is not an IRI of a model
    element with that local name denotes no element of the model: it is replaced by its full IRI, written <...>,
    which the checks then find undefined (as the SPARQL encoding does). A value that no longer matches the IRIs
    written in its field (rewritten by identity migration) is left as it is. Returns the resolved requirement
    and {local name: IRIs that the model does not contain}."""
    foreign: Dict[str, Set[str]] = {}

    def res(iri: str) -> str:
        name = local(iri)
        model = env.iris.get(name)
        if model and iri not in model:
            foreign.setdefault(name, set()).add(iri)
            return f"<{iri}>"
        return name
    resolved: Dict[str, Dict[str, List[str]]] = {}
    for attr, iris in q.field_iris.items():
        for i in iris:
            resolved.setdefault(attr, {}).setdefault(local(i), []).append(res(i))
    if not foreign:
        return q, {}
    from copy import deepcopy
    q2 = deepcopy(q)
    for attr in ("seed_goal", "context", "actor", "authority_of", "interaction", "interaction_source"):
        v = getattr(q2, attr)
        if v in resolved.get(attr, {}):
            setattr(q2, attr, resolved[attr][v][0])
    for attr in ("actions", "concepts", "properties", "hard", "soft"):
        out: List[str] = []
        for v in getattr(q2, attr):
            out.extend(resolved.get(attr, {}).get(v, [v]))
        setattr(q2, attr, sorted(set(out)))
    return q2, {k: sorted(v) for k, v in sorted(foreign.items())}


def _iris_of(ids: Iterable[str], env: Environment, q: Requirement) -> Dict[str, List[str]]:
    out = {}
    for e in ids:
        if e.startswith("<") and e.endswith(">"):
            out[e] = [e[1:-1]]
        elif e in env.iris:
            out[e] = env.iris[e]
        elif e in env.all_iris:
            out[e] = env.all_iris[e]
        elif e in q.ref_iris:
            out[e] = q.ref_iris[e]
    return out


def validate(env: Environment, q: Requirement) -> Result:
    hard_fail, hard_unchecked, soft_res, open_u = [], [], [], []
    premises: Dict[str, str] = {}
    cand = candidate_snapshot(q)
    q_in = q

    def done(state, just, a=None, log=()):
        trace = a.trace() if a else set()
        return Result(q_in.rid, state, just, trace, list(log), env.snapshot, premises=premises, candidate=cand,
                      iris=_iris_of(trace, env, q_in), req_iri=q_in.iri)

    # An ill-formed annotation (several values for a field that takes one, or a value of the wrong kind) is
    # not interpreted: the contract does not choose between values. The decision does not depend on the
    # model (empty trace).
    problems = annotation_problems(q)
    if problems:
        return done(PENDING_REVIEW, [f"PENDING REVIEW: {m}" for m in problems])
    q, foreign_refs = resolve_references(env, q)
    a = Assessment(env, q)
    a.read(*sorted(foreign_refs))  # the local names were consulted to find that the IRIs are not in the model

    def ob(level, outcome, msg):
        a.record(level, outcome, msg)
        if outcome == FAIL and level == "hard":
            hard_fail.append(msg)
        elif outcome == UNCHECKED and level == "hard":
            hard_unchecked.append(msg)
        elif outcome in (FAIL, UNCHECKED) and level == "soft":
            soft_res.append(msg)

    a.read(q.seed_goal, q.context)
    if q.seed_goal not in env.goals or q.context not in env.contexts:
        return done(NOT_DERIVED, [f"NOT DERIVED: seed {q.seed_goal} or context {q.context} undefined"], a)
    if not q.references():  # refs(q): actor, authority holder, actions, concepts, properties, interaction, source
        return done(PENDING_REVIEW, ["PENDING REVIEW: the annotation references no environment element"], a)
    lineage = seed_lineage(a)
    ctx = env.contexts[q.context]
    # Well-formedness guard: a refinement cycle through the seed makes the
    # lineage (and therefore blocking) ill-defined.
    if any(q.seed_goal in env.goals.get(g, {}).get("refines", []) for g in lineage):
        ob("hard", UNCHECKED, f"goal refinement cycle through {q.seed_goal} (WF(E) violated)")

    # O1 Actor: existence and participation in the assessment situation.
    actor = None
    if q.actor:
        a.read(q.actor)
        actor = env.actors.get(q.actor)
        if actor is None:
            ob("hard", FAIL, f"actor {q.actor} undefined")
        else:
            active = q.actor in ctx["activeActors"]
            responsible = bool(set(actor["responsibleFor"]) & set(lineage))
            if active or responsible:
                ob("hard", PASS, f"actor {q.actor} participates "
                   f"({'active in ' + q.context if active else 'responsible for the seed lineage'})")
            else:
                ob("hard", FAIL, f"actor {q.actor} is neither active in {q.context} nor responsible for the seed lineage")
    # O2 Capabilities. A capability is a relation between an actor and an action: without a
    # responsible actor in the annotation the obligation cannot be decided (hard, unchecked).
    for act in q.actions:
        a.read(act)
        if act not in env.actions:
            ob("hard", FAIL, f"action {act} undefined")
        elif q.actor is None:
            ob("hard", UNCHECKED, f"capability for action {act} cannot be checked: "
               "the annotation names no responsible actor")
        elif actor is not None:
            if act in actor["capabilities"]:
                ob("hard", PASS, f"actor {q.actor} has capability {act}")
            else:
                ob("hard", FAIL, f"actor {q.actor} lacks capability {act}")
    # O3 Authority presence.
    if q.authority_of:
        a.read(q.authority_of)
        holder = env.actors.get(q.authority_of)
        if holder is None:
            ob("hard", FAIL, f"authority holder {q.authority_of} undefined")
        elif one(holder["authority"]):
            ob("hard", PASS, f"{q.authority_of} has an authority level")
        else:
            ob("hard", FAIL, f"{q.authority_of} has no authority level")
    # O4 Domain concepts and O5 properties.
    for cid in q.concepts:
        a.read(cid)
        ok = cid in env.domain
        ob("hard", PASS if ok else FAIL, f"domain concept {cid} {'defined' if ok else 'undefined'}")
    for pid in q.properties:
        a.read(pid)
        owners = [c for c in q.concepts if pid in env.domain.get(c, {}).get("properties", [])]
        if pid not in env.properties:
            ob("hard", FAIL, f"domain property {pid} undefined")
        elif not owners:
            ob("hard", FAIL, f"domain property {pid} is not a property of any referenced concept")
        else:
            ob("hard", PASS, f"domain property {pid} belongs to {owners[0]}")
    # O6 Interaction: existence, endpoint, direction, exchanged data, protocol.
    if q.interaction:
        a.read(q.interaction)
        it = env.interactions.get(q.interaction)
        if it is None:
            ob("hard", FAIL, f"interaction {q.interaction} undefined")
        else:
            src, tgt = one(it["source"]), one(it["target"])
            problems = []
            ambiguous_ends = len(it["source"]) > 1 or len(it["target"]) > 1
            if ambiguous_ends:
                # Endpoint and direction checks would rest on an arbitrary choice
                # among conflicting values: they are not decided (see O9).
                ob("hard", UNCHECKED, f"{q.interaction} has conflicting source/target values; "
                   "endpoint and direction checks not decided")
            if not ambiguous_ends and q.actor and q.actor not in it["source"] + it["target"]:
                problems.append(f"actor {q.actor} is not an endpoint of {q.interaction}")
            if not ambiguous_ends and q.interaction_source and src != q.interaction_source:
                problems.append(f"{q.interaction} originates from {src}, expected {q.interaction_source}")
            if q.concepts and not (set(it["data"]) & set(q.concepts)):
                problems.append(f"{q.interaction} exchanges none of the referenced concepts")
            if q.requires_protocol and not one(it["protocol"]):
                problems.append(f"{q.interaction} declares no protocol")
            for msg in problems:
                ob("hard", FAIL, msg)
            if not problems and not ambiguous_ends:
                held = [n for n, on in (("endpoint", bool(q.actor)), ("direction", bool(q.interaction_source)),
                                        ("exchanged-data", bool(q.concepts)), ("protocol", bool(q.requires_protocol)))
                        if on]
                if held:
                    ob("hard", PASS, f"interaction {q.interaction} ({src} -> {tgt}) exists; "
                       f"{', '.join(held)} check(s) hold")
                else:
                    ob("hard", PASS, f"interaction {q.interaction} ({src} -> {tgt}) exists; the annotation "
                       "requests no endpoint, direction, data, or protocol check")
    elif q.interaction_source or q.requires_protocol:
        a.read(q.interaction_source)
        ob("hard", UNCHECKED, "an expected interaction source or a required protocol is declared without a "
           "used interaction; not checked")

    # O7 Applicable constraints, discharged by registered validators.
    routes = applicable_constraints(a, lineage)
    for cid in sorted(routes):
        a.read(cid)
        c = env.constraints.get(cid)
        declared_soft = cid in q.soft
        if c is None:
            ob("soft" if declared_soft else "hard", UNCHECKED, f"constraint {cid} (via {routes[cid]}) undefined")
            continue
        if cid in env.conflicts:
            ob("hard", UNCHECKED, f"constraint {cid} has conflicting values {env.conflicts[cid]}; not evaluated")
            continue
        level = (one(c["enforcement"]) or ("soft" if declared_soft else "hard")).strip().lower()
        level = "soft" if level == "soft" else "hard"
        for x in c["exceptions"]:
            a.read(x)
        validator = VALIDATORS.get(one(c["validatorKey"]) or "")
        if validator is None:
            ob(level, UNCHECKED, f"{level} constraint {cid} (via {routes[cid]}) has no registered validator")
            continue
        outcome, msg = validator(a, cid, c)
        if outcome == NOT_TRIGGERED:
            a.record(level, NOT_TRIGGERED, msg)
        else:
            ob(level, outcome, msg)

    # O8 Uncertainties blocking the seed lineage or affecting consulted evidence.
    evidence = q.references() | set(lineage) | set(routes) | {q.context}
    # blocks/affects links from elements not typed as uncertainties: their subjects are read (type only).
    for e in sorted(lineage):
        a.read_type(*env.foreign.get("blocks", {}).get(e, []))
    for e in sorted(evidence):
        a.read_type(*env.foreign.get("affects", {}).get(e, []))
    for uid, u in sorted(env.uncertainties.items()):
        blocking = sorted(set(u["blocks"]) & set(lineage))
        affecting = sorted(set(u["affects"]) & evidence)
        if not blocking and not affecting:
            continue
        a.read(uid)
        if uid in env.conflicts:
            ob("hard", UNCHECKED, f"{uid} has conflicting values {env.conflicts[uid]}; not interpreted")
            premises[uid] = UNCHECKED
            continue
        status, impact = one(u["status"]), (one(u["impact"]) or "high")
        if blocking:
            if status == "unvalidated":
                open_u.append(uid); premises[uid] = "open"
                a.record("hard", "open", f"{uid} (unvalidated) blocks {blocking[0]}")
            elif status == "validated":
                premises[uid] = PASS
                a.record("hard", PASS, f"{uid} blocking {blocking[0]} is validated")
            elif status == "rejected":
                premises[uid] = FAIL
                ob("hard", FAIL, f"{uid} blocking {blocking[0]} was refuted (status rejected)")
            else:
                premises[uid] = UNCHECKED
                ob("hard", UNCHECKED, f"{uid} blocking {blocking[0]} has uninterpretable status {status!r}")
        else:
            if status == "unvalidated":
                if impact in ("high", "critical"):
                    open_u.append(uid); premises[uid] = "open"
                    a.record("hard", "open", f"{uid} (unvalidated, {impact}) affects consulted evidence {affecting}")
                else:
                    premises[uid] = "soft-fail"
                    ob("soft", FAIL, f"{uid} (unvalidated, {impact}) affects consulted evidence {affecting}")
            elif status == "validated":
                premises[uid] = PASS
                a.record("soft", PASS, f"{uid} affecting {affecting} is validated")
            elif status == "rejected":
                premises[uid] = UNCHECKED
                ob("hard", UNCHECKED, f"{uid} invalidates consulted evidence {affecting}; review required")
            else:
                premises[uid] = UNCHECKED
                ob("hard", UNCHECKED, f"{uid} affecting {affecting} has uninterpretable status {status!r}")

    # O9 Conflicting evidence on any consulted element routes to review.
    for eid in sorted(a.reads):
        if eid in env.conflicts:
            ob("hard", UNCHECKED, f"conflicting values for {eid}: {env.conflicts[eid]}")
        if eid in env.multi_typed:
            ob("hard", UNCHECKED, f"{eid} is typed in several views: {env.multi_typed[eid]}")
        if eid in env.collisions:
            ob("hard", UNCHECKED, f"identifier {eid} denotes several IRIs {env.collisions[eid]}; "
               "no check on it is decided")

    if hard_fail:
        state = REJECTED
    elif hard_unchecked:
        state = PENDING_REVIEW
    elif open_u:
        state = CONDITIONAL
    elif soft_res:
        state = ACCEPTED_WITH_RESERVATIONS
    else:
        state = ACCEPTED
    prefix = {PASS: "OK", FAIL: "FAIL", UNCHECKED: "UNCHECKED", NOT_TRIGGERED: "NOT TRIGGERED", "open": "OPEN"}
    just = [f"{prefix.get(o, o.upper())} [{lvl}]: {m}" for lvl, o, m in a.log]
    return done(state, just, a, a.log)


def candidate_snapshot(q: Requirement) -> str:
    """SHA-256 of the candidate statement q and its annotation alpha(q), canonically serialized."""
    import json
    d = {"rid": q.rid, "statement": q.text, "seed_goal": q.seed_goal, "context": q.context, "actor": q.actor,
         "actions": sorted(q.actions), "authority_of": q.authority_of, "concepts": sorted(q.concepts),
         "properties": sorted(q.properties), "interaction": q.interaction,
         "interaction_source": q.interaction_source, "requires_protocol": bool(q.requires_protocol),
         "hard": sorted(q.hard), "soft": sorted(q.soft)}
    if q.field_iris:  # the IRIs written in each field (two namespaces with one local name are different annotations)
        d["iris"] = {k: sorted(v) for k, v in sorted(q.field_iris.items())}
    if q.conflicts:  # an ill-formed annotation is identified with all its values
        d["conflicts"] = {k: sorted(v) for k, v in q.conflicts.items()}
    if q.illformed:
        d["illformed"] = {k: sorted(v) for k, v in q.illformed.items()}
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Change impact
# ---------------------------------------------------------------------------
def touched_identifiers(g_old: rdflib.Graph, g_new: rdflib.Graph) -> Set[str]:
    """Identifiers occurring as subject or IRI object (other than an rdf:type class) of a
    triple added to or removed from the environment snapshot."""
    a, b = environment_triples(g_old), environment_triples(g_new)
    out: Set[str] = set()
    for s, p, o in a ^ b:
        out.add(local(s))
        if isinstance(o, URIRef) and p != RDF.type:
            out.add(local(o))
    return out


def subject_identifiers(g_old: rdflib.Graph, g_new: rdflib.Graph) -> Set[str]:
    """Element-level diff that records only modified subjects (coarser)."""
    return {local(s) for s, _, _ in environment_triples(g_old) ^ environment_triples(g_new)}


def direct_reevaluation(delta: Set[str], records: Dict[str, Set[str]]) -> Set[str]:
    """DirectReEval(Δ) = { r | T_r ∩ Δ ≠ ∅ }."""
    return {rid for rid, trace in records.items() if trace & delta}


SCHEMA_PREDICATES = {RDFS.subClassOf, RDFS.subPropertyOf, RDFS.domain, RDFS.range, OWL.equivalentClass,
                     OWL.equivalentProperty, OWL.disjointWith, OWL.inverseOf, OWL.propertyDisjointWith}
SCHEMA_TYPES = {OWL.Class, RDFS.Class, OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty,
                RDF.Property, OWL.Restriction, OWL.AllDisjointClasses, OWL.Ontology, OWL.FunctionalProperty,
                OWL.InverseFunctionalProperty, OWL.TransitiveProperty, OWL.SymmetricProperty}


def vocabulary_fingerprint(g: rdflib.Graph) -> str:
    """SHA-256 of the vocabulary part of a graph: schema axioms, declared classes and properties, and every
    blank-node structure (canonicalized, so the fingerprint is deterministic)."""
    from rdflib.compare import to_canonical_graph
    decl = {s for t in SCHEMA_TYPES for s in g.subjects(RDF.type, t)}
    voc = rdflib.Graph()
    for s, p, o in g:
        if p in SCHEMA_PREDICATES or s in decl or isinstance(s, rdflib.BNode) or isinstance(o, rdflib.BNode):
            voc.add((s, p, o))
    lines = sorted(f"{s.n3()} {p.n3()} {o.n3()} ." for s, p, o in to_canonical_graph(voc))
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def select_for_reassessment(g_old: rdflib.Graph, g_new: rdflib.Graph,
                            records: Dict[str, Set[str]]) -> Tuple[Set[str], str]:
    """Selection used after a change. A change of the vocabulary contained in the snapshot falls outside the
    assumptions of Proposition 2, so every requirement is selected; otherwise DirectReEval on the touched
    identifiers. Returns (selected requirement ids, reason)."""
    if vocabulary_fingerprint(g_old) != vocabulary_fingerprint(g_new):
        return set(records), "vocabulary"
    return direct_reevaluation(touched_identifiers(g_old, g_new), records), "trace"


def requirements_of(element: str, records: Dict[str, Set[str]]) -> Set[str]:
    return {rid for rid, trace in records.items() if element in trace}


def dependency_closure(env: Environment, delta: Set[str]) -> Set[str]:
    """Transitive dependency closure used with coarse (view-level) traces:
    auxiliary elements are lifted to their owners (property/invariant -> concept,
    action -> actors holding it), and goal changes propagate to sub-goals."""
    owners: Dict[str, Set[str]] = {}
    for cid, c in env.domain.items():
        for x in c["properties"] + c["invariants"]:
            owners.setdefault(x, set()).add(cid)
    for aid, act in env.actors.items():
        for x in act["capabilities"]:
            owners.setdefault(x, set()).add(aid)
    children: Dict[str, Set[str]] = {}
    for gid, gd in env.goals.items():
        for p in gd["refines"]:
            children.setdefault(p, set()).add(gid)
    out, frontier = set(delta), list(delta)
    while frontier:
        x = frontier.pop()
        for y in owners.get(x, set()) | children.get(x, set()):
            if y not in out:
                out.add(y)
                frontier.append(y)
    return out


# ---------------------------------------------------------------------------
# Identity migration (rename / split / merge) through reo:supersededBy
# ---------------------------------------------------------------------------
def supersession_map(g: rdflib.Graph) -> Dict[str, List[str]]:
    m: Dict[str, List[str]] = {}
    for s, o in g.subject_objects(REO.supersededBy):
        m.setdefault(local(s), []).append(local(o))
    return {k: sorted(v) for k, v in m.items()}


# View in which each annotation field expects its element (the ranges of reo-derivation.ttl).
FIELD_VIEW = {"seed_goal": "goals", "context": "contexts", "actor": "actors", "authority_of": "actors",
              "interaction": "interactions", "interaction_source": "actors", "actions": "actions",
              "properties": "properties", "hard": "constraints", "soft": "constraints", "concepts": "domain"}
MIGRATION_REVIEW = ("AMBIGUOUS", "INVALID")   # migration notes that send the requirement to review


def migrate_requirement(q: Requirement, env: Environment, mapping: Dict[str, List[str]]) -> Tuple[Requirement, List[str]]:
    """Rewrite references through the supersession map (one step). A split concept is
    resolved by the referenced properties: the reference is replaced by the successors that
    hold them. Any other split, a split concept without referenced properties, and a
    successor that is itself superseded are reported as ambiguous. A successor that is not an element
    of the view the field expects (it is missing from the snapshot, or typed in another view) is reported as
    invalid: the reference is not rewritten."""
    from copy import deepcopy
    q2, notes = deepcopy(q), []

    def valid(t, view):
        return t in env.views[view]

    def succ_single(x, view):
        s = mapping.get(x)
        if not s:
            return x
        if any(t in mapping for t in s):  # successor itself superseded: only one step is migrated
            notes.append(f"AMBIGUOUS supersession chain {x} -> {s}: a successor is itself superseded")
            return x
        if len(s) == 1:
            if not valid(s[0], view):
                notes.append(f"INVALID supersession {x} -> {s[0]}: the successor is not an element of the "
                             f"{view} view in the snapshot")
                return x
            notes.append(f"renamed/merged {x} -> {s[0]}")
            return s[0]
        return None  # split: decided by caller

    for attr in ("seed_goal", "context", "actor", "authority_of", "interaction", "interaction_source"):
        v = getattr(q2, attr)
        if v:
            r = succ_single(v, FIELD_VIEW[attr])
            if r is None:
                notes.append(f"AMBIGUOUS split of {v} -> {mapping[v]}")
                setattr(q2, attr, v)  # left unresolved
            else:
                setattr(q2, attr, r)
    for attr in ("actions", "properties", "hard", "soft"):
        new = []
        for v in getattr(q2, attr):
            r = succ_single(v, FIELD_VIEW[attr])
            if r is None:
                notes.append(f"AMBIGUOUS split of {v} -> {mapping[v]}")
                new.append(v)
            else:
                new.append(r)
        setattr(q2, attr, sorted(set(new)))
    concepts = []
    for c in q2.concepts:
        s = mapping.get(c)
        if not s:
            concepts.append(c)
        elif any(t in mapping for t in s):
            notes.append(f"AMBIGUOUS supersession chain {c} -> {s}: a successor is itself superseded")
            concepts.append(c)
        elif len(s) == 1:
            if not valid(s[0], "domain"):
                notes.append(f"INVALID supersession {c} -> {s[0]}: the successor is not an element of the "
                             "domain view in the snapshot")
                concepts.append(c)
            else:
                notes.append(f"renamed/merged {c} -> {s[0]}")
                concepts.append(s[0])
        else:
            owners = sorted({o for o in s for p in q2.properties if p in env.domain.get(o, {}).get("properties", [])})
            if len(owners) >= 1 and q2.properties:
                notes.append(f"split {c} resolved by referenced properties -> {owners}")
                concepts.extend(owners)
            else:
                notes.append(f"AMBIGUOUS split of {c} -> {s}")
                concepts.append(c)
    q2.concepts = sorted(set(concepts))
    return q2, notes


def _level(outcome: str) -> str:
    """Level of a premise outcome that is not pass: soft for an unvalidated premise of low or medium impact
    that affects evidence (soft-fail), hard otherwise."""
    return "soft" if outcome == "soft-fail" else "hard"


def reassess(env: Environment, q: Requirement, mapping: Optional[Dict[str, List[str]]] = None,
             previous: Optional[Result] = None, previous_env: Optional[Environment] = None) -> Result:
    """Re-assessment entry point (Sections VI-B and VI-C).
    1. Identity migration: references are rewritten through reo:supersededBy (one step); a split that the
       referenced properties do not resolve, a successor that is itself superseded, or a successor that is not
       an element of the expected view in the snapshot sends q to review.
    2. Fresh assessment of the new snapshot (validate).
    3. Premise-removal rule (needs the previous assessment, in memory or read back from its record with
       previous_from_records): a premise whose O8 outcome was not pass in the previous assessment and that
       no longer exists in the snapshot (and has no existing successor) is not treated as resolved. It adds an
       unchecked outcome at the level of its previous outcome: hard (open, refuted, uninterpretable) -> review
       unless a hard check fails; soft (unvalidated, low or medium impact) -> a reservation, which never
       prevents acceptance. The premise stays unresolved in the new assessment (and in its record), so the
       rule applies again at the next re-assessment. If the previous snapshot is supplied (previous_env), a
       removed premise counts only if its links in that snapshot reach an element read by the new assessment;
       otherwise every removed premise counts (conservative). When the new decision does not rest on the
       model (seed or context undefined, ill-formed annotation), the rule is not applied and the removed
       premises are carried over unchanged. Without `previous` and without supersession links this is exactly
       validate(env, q)."""
    from dataclasses import replace
    mapping = mapping or {}
    notes: List[str] = []
    if mapping:
        q2, notes = migrate_requirement(q, env, mapping)
        if any(n.startswith(MIGRATION_REVIEW) for n in notes):
            r0 = validate(env, q)
            r = replace(r0, state=PENDING_REVIEW, justifications=notes + r0.justifications)
        else:
            r = validate(env, q2)
            r = replace(r, justifications=notes + r.justifications)
    else:
        r = validate(env, q)
    if previous is None:
        return r
    removed = [(u, o) for u, o in sorted(previous.premises.items())
               if o != PASS and u not in env.uncertainties
               and not any(s in env.uncertainties for s in mapping.get(u, []))]
    if not removed:
        return r
    premises, iris = dict(r.premises), dict(r.iris)
    for u, _ in removed:
        if u not in iris:
            iris[u] = previous.iris.get(u) or env.all_iris.get(u) or []
    trace = set(r.trace) | {u for u, _ in removed}
    if r.state == NOT_DERIVED or annotation_problems(q):
        for u, o in removed:
            premises[u] = "soft-fail" if _level(o) == "soft" else UNCHECKED
        return replace(r, premises=premises, iris=iris, trace=trace)
    extra, quiet = [], []
    for u, o in removed:
        lvl = _level(o)
        if previous_env is not None and u in previous_env.uncertainties:
            old = previous_env.uncertainties[u]
            if not (set(old["blocks"]) | set(old["affects"])) & r.trace:
                quiet.append((lvl, NOT_TRIGGERED, f"premise {u} ({lvl}, not established in the previous "
                              "assessment) no longer exists; in the previous snapshot it bore on no element read "
                              "by this assessment"))
                continue
        extra.append((lvl, UNCHECKED, f"premise {u} ({lvl}, not established in the previous assessment) no longer "
                      "exists; its resolution was not recorded"))
        premises[u] = "soft-fail" if lvl == "soft" else UNCHECKED
    state = r.state
    if any(lvl == "hard" for lvl, _, _ in extra):
        state = r.state if r.state == REJECTED else PENDING_REVIEW
    elif extra:  # soft only: a reservation, which never prevents acceptance
        state = ACCEPTED_WITH_RESERVATIONS if r.state == ACCEPTED else r.state
    prefix = {UNCHECKED: "UNCHECKED", NOT_TRIGGERED: "NOT TRIGGERED"}
    return replace(r, state=state, justifications=r.justifications + [f"{prefix[o]} [{lvl}]: {m}"
                                                                     for lvl, o, m in extra + quiet],
                   trace=trace, obligations=list(r.obligations) + extra + quiet, premises=premises, iris=iris)


def rederive(env: Environment, q: Requirement, mapping: Dict[str, List[str]]) -> Result:
    """Identity-aware re-assessment without history (kept for the v1.1 runners): reassess(env, q, mapping)."""
    return reassess(env, q, mapping)


# ---------------------------------------------------------------------------
# PROV-O decision records
# ---------------------------------------------------------------------------
def _record_namespace(r: Result, requirements_ns=None) -> Namespace:
    """Records live next to the requirement they assess: <namespace of the requirement>/records#."""
    if requirements_ns is not None:
        base = str(requirements_ns)
    elif r.req_iri:
        base = r.req_iri[:len(r.req_iri) - len(local(r.req_iri))]
    else:
        base = str(REQ)
    return Namespace(base.rstrip("#/") + "/records#")


def record_iri(r: Result, requirements_ns=None) -> URIRef:
    """Record identity = (requirement, environment snapshot, candidate snapshot, contract version), so two
    annotations of one candidate on one snapshot never share a record."""
    key = hashlib.sha256(f"{r.snapshot}|{r.candidate}|{POLICY_VERSION}".encode("utf-8")).hexdigest()[:16]
    return _record_namespace(r, requirements_ns)[f"{r.rid}-{key}"]


# Namespace for an identifier for which no IRI is known (only an annotation value given as a literal, which the
# well-formedness guard rejects, can produce one).
UNRESOLVED = Namespace("urn:reo:unresolved#")


def _iris_for(r: Result, e: str) -> List[URIRef]:
    return [URIRef(i) for i in r.iris.get(e, [])] or [UNRESOLVED[e]]


def record_graph(results: List[Result], requirements_ns=None, timestamp: str = "2026-10-07T00:00:00Z",
                 previous: Optional[Dict[str, Result]] = None) -> rdflib.Graph:
    """PROV-O decision records. The record names the assessed requirement and the consulted elements by their
    IRIs. Each premise that was not established is written with reo:unresolvedHardPremise or
    reo:unresolvedSoftPremise (sub-properties of reo:unresolvedPremise), so that the premise-removal rule can be
    applied to a record read back with previous_from_records. `previous` maps a requirement id to the assessment
    it replaces (in memory or read back); the new record is then linked to the previous record(s) with
    reo:supersedes. The fixed default timestamp keeps the generated graphs reproducible."""
    g = rdflib.Graph()
    g.bind("reo", REO); g.bind("prov", PROV); g.bind("req", REQ); g.bind("ex", EX)
    for k, r in enumerate(results):
        rec = record_iri(r, requirements_ns)
        if k == 0:
            g.bind("rec", _record_namespace(r, requirements_ns))
        act = URIRef(str(rec) + "-validation")
        g.add((rec, RDF.type, REO.DerivationRecord))
        g.add((rec, REO.assesses, URIRef(r.req_iri) if r.req_iri else
               Namespace(str(requirements_ns or REQ))[r.rid]))
        g.add((rec, REO.hasDecision, STATE_IRI[r.state]))
        g.add((rec, REO.environmentSnapshot, Literal(r.snapshot)))
        g.add((rec, REO.candidateSnapshot, Literal(r.candidate)))
        g.add((rec, REO.policyVersion, Literal(POLICY_VERSION)))
        if previous and r.rid in previous:
            p = previous[r.rid]
            for prev in (p.records or (str(record_iri(p, requirements_ns)),)):
                if URIRef(prev) != rec:
                    g.add((rec, REO.supersedes, URIRef(prev)))
        g.add((rec, PROV.wasGeneratedBy, act))
        g.add((act, RDF.type, REO.ValidationActivity))
        g.add((act, PROV.startedAtTime, Literal(timestamp, datatype=rdflib.XSD.dateTime)))
        for j in r.justifications:
            g.add((rec, REO.hasJustification, Literal(j)))
        for e in sorted(r.trace):
            for i in _iris_for(r, e):
                g.add((act, REO.consulted, i))
        for u, o in sorted(r.premises.items()):
            if o != PASS:
                sub = REO.unresolvedSoftPremise if _level(o) == "soft" else REO.unresolvedHardPremise
                for i in _iris_for(r, u):
                    g.add((act, REO.unresolvedPremise, i))
                    g.add((act, sub, i))
    return g


def _result_from_record(g: rdflib.Graph, rec, req_iri: str, rid: str) -> Result:
    states = {v: k for k, v in STATE_IRI.items()}
    acts = list(g.objects(rec, PROV.wasGeneratedBy))

    def objs(p):
        return {i for a in acts for i in g.objects(a, p)}
    hard, soft, generic = objs(REO.unresolvedHardPremise), objs(REO.unresolvedSoftPremise), objs(REO.unresolvedPremise)
    iris: Dict[str, Set[str]] = {}
    for i in objs(REO.consulted) | hard | soft | generic:
        iris.setdefault(local(i), set()).add(str(i))
    premises: Dict[str, str] = {}
    for i in sorted(hard | soft | generic):
        # A premise written without its level (records of Baseline II) is read as hard (conservative).
        lvl = "soft" if (i in soft and i not in hard) else "hard"
        if premises.get(local(i)) != UNCHECKED:
            premises[local(i)] = "soft-fail" if lvl == "soft" else UNCHECKED
    return Result(rid=rid, state=states.get(g.value(rec, REO.hasDecision), PENDING_REVIEW),
                  justifications=sorted(str(j) for j in g.objects(rec, REO.hasJustification)),
                  trace=set(iris), obligations=[], snapshot=str(g.value(rec, REO.environmentSnapshot) or ""),
                  premises=premises, candidate=str(g.value(rec, REO.candidateSnapshot) or ""),
                  iris={k: sorted(v) for k, v in sorted(iris.items())}, req_iri=req_iri, records=(str(rec),))


def latest_records(g: rdflib.Graph) -> Dict[str, List[str]]:
    """{requirement IRI: its latest record(s)}: the records of the requirement that no other record of the
    requirement supersedes. Several latest records (a branched history), or none (a supersession cycle, which
    the shapes reject), are all returned."""
    by_req: Dict[str, Set] = {}
    for rec in g.subjects(RDF.type, REO.DerivationRecord):
        for q in g.objects(rec, REO.assesses):
            by_req.setdefault(str(q), set()).add(rec)
    out = {}
    for q, recs in sorted(by_req.items()):
        superseded = {p for r in recs for p in g.objects(r, REO.supersedes) if p in recs}
        heads = sorted(recs - superseded) or sorted(recs)
        out[q] = [str(h) for h in heads]
    return out


def previous_from_records(g: rdflib.Graph, requirements: Optional[Iterable[Requirement]] = None) -> Dict[str, Result]:
    """Read back, for each requirement, the assessment stated by its latest record, as the `previous` argument of
    reassess and record_graph: decision, justifications, trace (local names of the consulted IRIs), the premises
    that were not established with their level, and the record IRI. Keys are requirement ids (from
    `requirements`, matched by IRI, or else from the record IRI <id>-<key>). With several latest records the
    assessments are merged: union of traces and premises (hard wins), and the decision if they agree (otherwise
    pending review)."""
    rid_of = {q.iri: q.rid for q in (requirements or [])}
    out: Dict[str, Result] = {}
    for q, heads in latest_records(g).items():
        rid = rid_of.get(q) or local(heads[0]).rsplit("-", 1)[0]
        rs = [_result_from_record(g, URIRef(h), q, rid) for h in heads]
        if len(rs) == 1:
            out[rid] = rs[0]
            continue
        premises: Dict[str, str] = {}
        for x in rs:
            for u, o in x.premises.items():
                if premises.get(u) != UNCHECKED:
                    premises[u] = o
        iris: Dict[str, Set[str]] = {}
        for x in rs:
            for k, v in x.iris.items():
                iris.setdefault(k, set()).update(v)
        states = {x.state for x in rs}
        just = sorted({j for x in rs for j in x.justifications})
        if len(states) > 1:
            just.append(f"PENDING REVIEW: several latest records with different decisions: {sorted(states)}")
        out[rid] = Result(rid=rid, state=states.pop() if len(states) == 1 else PENDING_REVIEW, justifications=just,
                          trace=set().union(*(x.trace for x in rs)), obligations=[],
                          snapshot=rs[0].snapshot if len({x.snapshot for x in rs}) == 1 else "",
                          premises=premises, candidate=rs[0].candidate if len({x.candidate for x in rs}) == 1 else "",
                          iris={k: sorted(v) for k, v in sorted(iris.items())}, req_iri=q,
                          records=tuple(h for x in rs for h in x.records))
    return out
