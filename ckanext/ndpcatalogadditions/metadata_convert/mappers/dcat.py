"""DCAT / DCAT-AP (RDF: Turtle, RDF/XML, JSON-LD with inline context) and DCAT-US JSON -> CKAN."""

from __future__ import annotations

import json
import re

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, FOAF, OWL, RDF, RDFS, SKOS

from ..ckan import CkanBuilder
from ..util import TJ, Tracker, extract_doi, is_url, json_leaf_items, pick_identifier, split_interval, tj_list

# Open namespace: rdflib's built-in DCAT is closed and lacks DCAT 3 terms (version, inSeries, DatasetSeries).
DCAT = Namespace("http://www.w3.org/ns/dcat#")
VCARD = Namespace("http://www.w3.org/2006/vcard/ns#")
LOCN = Namespace("http://www.w3.org/ns/locn#")
ADMS = Namespace("http://www.w3.org/ns/adms#")
SCHEMA = Namespace("http://schema.org/")
SDO = Namespace("https://schema.org/")
TIME = Namespace("http://www.w3.org/2006/time#")
GSP = Namespace("http://www.opengis.net/ont/geosparql#")

_PREFIXES = {
    str(DCAT): "dcat", str(DCTERMS): "dct", str(FOAF): "foaf", str(VCARD): "vcard", str(LOCN): "locn",
    str(ADMS): "adms", str(SCHEMA): "schema", str(SDO): "schema", str(OWL): "owl", str(RDFS): "rdfs",
    str(SKOS): "skos", str(RDF): "rdf", "http://www.w3.org/ns/prov#": "prov",
    "http://data.europa.eu/r5r/": "dcatap", "http://www.w3.org/2002/07/owl#": "owl",
    "http://data.europa.eu/930/": "geodcatap", "http://www.w3.org/ns/dqv#": "dqv",
}


def convert(parsed, b: CkanBuilder):
    if parsed.kind == "json" and not isinstance(parsed.data.get("@context") if isinstance(parsed.data, dict) else None, dict):
        _convert_json(parsed.data, b)
        return
    g = Graph()
    fmt = parsed.rdf_format or ("json-ld" if parsed.kind == "json" else "xml")
    g.parse(data=parsed.text, format=fmt)
    datasets = sorted(set(g.subjects(RDF.type, DCAT.Dataset)), key=str) \
        or sorted(set(g.subjects(RDF.type, DCAT.DatasetSeries)), key=str)
    if not datasets:
        raise ValueError("no dcat:Dataset or dcat:DatasetSeries found in RDF graph")
    if len(datasets) > 1:
        b.warn(f"graph contains {len(datasets)} dcat:Dataset nodes; converted {datasets[0]}")
    _RdfMapper(g, b).map(datasets[0])


# ---------------------------------------------------------------------------
# RDF
# ---------------------------------------------------------------------------


class _RdfMapper:
    def __init__(self, g: Graph, b: CkanBuilder):
        self.g, self.b = g, b
        self.used: set[tuple[str, URIRef]] = set()
        self.lang: str | None = None

    def qname(self, p: URIRef) -> str:
        s = str(p)
        for ns, prefix in _PREFIXES.items():
            if s.startswith(ns):
                return f"{prefix}:{s[len(ns):]}"
        try:  # prefixes declared in the source document
            prefix, _, local = self.g.namespace_manager.compute_qname(s, generate=False)
            return f"{prefix}:{local}" if prefix else local
        except (KeyError, ValueError):
            return s

    def objs(self, s, *preds, role="dataset") -> list:
        out = []
        for p in preds:
            vals = sorted(self.g.objects(s, p), key=lambda n: _node_key(self.g, n))
            if vals:
                self.used.add((role, p))
            out.extend(v for v in vals if v not in out)
        return out

    def lit(self, s, *preds, role="dataset") -> str | None:
        return _best_literal(self.objs(s, *preds, role=role), self.lang)

    def lits(self, s, *preds, role="dataset") -> list[str]:
        """All values in the preferred language (keywords etc. are often given in 20+ languages)."""
        vals = self.objs(s, *preds, role=role)
        by_lang: dict[str | None, list[str]] = {}
        for v in vals:
            lang = v.language if isinstance(v, Literal) else None
            by_lang.setdefault(lang, []).append(str(v))
        for lang in (self.lang, None):
            if lang in by_lang:
                return by_lang[lang] + ([x for x in by_lang.get(None, [])] if lang else [])
        if not by_lang:
            return []
        return max(by_lang.values(), key=len)

    def name_of(self, node) -> str | None:
        if isinstance(node, Literal):
            return str(node)
        name = _best_literal(
            [o for p in (FOAF.name, VCARD.fn, VCARD["organization-name"], SKOS.prefLabel, RDFS.label, SCHEMA.name, SDO.name)
             for o in sorted(self.g.objects(node, p), key=lambda n: _node_key(self.g, n))], self.lang)
        return name or (str(node) if isinstance(node, URIRef) else None)

    def describe(self, node) -> str | None:
        """Text for an RDF value: literal/IRI as is, blank node as JSON of its properties."""
        if not isinstance(node, BNode):
            return str(node)
        value = self._bnode_dict(node, 0)
        return json.dumps(value, ensure_ascii=False) if value else None

    def _bnode_dict(self, node, depth: int):
        props: dict[str, list] = {}
        for p, o in sorted(self.g.predicate_objects(node), key=lambda po: (str(po[0]), _node_key(self.g, po[1]))):
            if p == RDF.type:
                continue
            v = (self._bnode_dict(o, depth + 1) if depth < 3 else None) if isinstance(o, BNode) else str(o)
            if v:
                props.setdefault(self.qname(p), []).append(v)
        return {k: v[0] if len(v) == 1 else v for k, v in props.items()}

    def website_of(self, node) -> str | None:
        for p in (FOAF.homepage, VCARD.hasURL, SCHEMA.url, SDO.url, FOAF.page):
            for o in sorted(self.g.objects(node, p), key=str):
                return str(o)
        return str(node) if isinstance(node, URIRef) and str(node).startswith("http") else None

    def email_of(self, node) -> str | None:
        for p in (VCARD.hasEmail, FOAF.mbox, SCHEMA.email):
            for o in sorted(self.g.objects(node, p), key=str):
                e = str(o)
                return e[7:] if e.startswith("mailto:") else e
        return None

    def map(self, ds):
        g, b = self.g, self.b
        titles = sorted(g.objects(ds, DCTERMS.title), key=lambda n: _node_key(g, n))
        langs = {t.language for t in titles if isinstance(t, Literal) and t.language}
        self.lang = "en" if "en" in langs else (sorted(langs)[0] if len(langs) == 1 else None)

        # dct:identifier is the publisher's own id; the subject URI is only a fallback.
        idents = [str(v) for v in self.objs(ds, DCTERMS.identifier)]
        b.external_id = pick_identifier(idents) or (str(ds) if isinstance(ds, URIRef) else None)
        doi = extract_doi(b.external_id)
        if doi:
            b.add_extra("doi", doi)
        if isinstance(ds, URIRef):
            b.add_extra("dataset_uri", str(ds))
        for other in self.objs(ds, ADMS.identifier):
            b.add_extra("other_identifiers", self.lit(other, SKOS.notation, role="identifier") or str(other))

        b.set_title(self.lit(ds, DCTERMS.title))
        for alt in self.lits(ds, DCTERMS.alternative):
            b.add_extra("alternate_title", alt)
        b.set_notes(self.lit(ds, DCTERMS.description))
        b.add_tags(self.lits(ds, DCAT.keyword))
        for theme in self.objs(ds, DCAT.theme):
            b.add_extra("theme", _local(theme) if isinstance(theme, URIRef) else self.name_of(theme))

        for pub in self.objs(ds, DCTERMS.publisher):
            b.set_publisher(self.name_of(pub), email=self.email_of(pub), website=self.website_of(pub))
            if isinstance(pub, URIRef):
                b.add_extra("publisher_uri", str(pub))
            for t in self.g.objects(pub, DCTERMS.type):
                b.add_extra("publisher_type", _local(t))
        for c in self.objs(ds, DCTERMS.creator):
            b.add_creator(self.name_of(c), email=self.email_of(c), identifier=self.website_of(c))
        for c in self.objs(ds, DCAT.contactPoint):
            b.add_contact(self.name_of(c), email=self.email_of(c), website=self.website_of(c))
        for c in self.objs(ds, DCTERMS.contributor):
            b.add_extra("contributors", self.name_of(c))

        b.set_issued(self.lit(ds, DCTERMS.issued) or self.lit(ds, DCTERMS.created))
        b.set_modified(self.lit(ds, DCTERMS.modified))
        b.add_extra("date_created", self.lit(ds, DCTERMS.created))
        b.set_field("version", self.lit(ds, DCAT.version, OWL.versionInfo, ADMS.version))
        b.add_extra("version_notes", self.lit(ds, ADMS.versionNotes))

        for lic in self.objs(ds, DCTERMS.license):
            b.add_license(str(lic) if isinstance(lic, URIRef) else self.name_of(lic))
        for r in self.objs(ds, DCTERMS.rights):
            b.add_license(self.name_of(r))
        for ar in self.objs(ds, DCTERMS.accessRights):
            b.add_extra("access_rights", _local(ar) if isinstance(ar, URIRef) else self.name_of(ar))

        for sp in self.objs(ds, DCTERMS.spatial):
            self._spatial(sp)
        for tp in self.objs(ds, DCTERMS.temporal):
            self._temporal(tp)
        for p, key in ((DCTERMS.language, "language"), (DCTERMS.accrualPeriodicity, "frequency"),
                       (DCTERMS.conformsTo, "conforms_to"),
                       (DCTERMS.isPartOf, "is_part_of"), (DCTERMS.hasPart, "has_part"),
                       (DCTERMS.source, "source"), (DCTERMS.relation, "relation"), (DCTERMS.references, "references"),
                       (DCAT.temporalResolution, "temporal_resolution"),
                       (DCTERMS.provenance, "provenance"), (DCAT.qualifiedRelation, "qualified_relation"),
                       (DCAT.inSeries, "in_series"), (OWL.sameAs, "same_as")):
            for v in self.objs(ds, p):
                if isinstance(v, BNode):
                    v = self.name_of(v) or self.lit(v, RDFS.label, role="other")
                elif isinstance(v, URIRef) and key in ("language", "frequency", "resource_type"):
                    v = _local(v)
                b.add_extra(key, str(v) if v is not None else None)

        for t in self.objs(ds, DCTERMS.type):
            b.set_resource_type(_local(t) if isinstance(t, URIRef) else self.name_of(t))
        if (ds, RDF.type, DCAT.DatasetSeries) in g:
            b.add_extra("dcat_type", "DatasetSeries")
        for res in self.objs(ds, DCAT.spatialResolutionInMeters):
            b.set_spatial_resolution(str(res), "meter")
        for page in self.objs(ds, FOAF.page):
            b.add_docs_url(str(page) if isinstance(page, URIRef) else self.website_of(page) or self.name_of(page))

        for lp in self.objs(ds, DCAT.landingPage):
            b.source_url = b.source_url or str(lp)
            b.set_landing_page(str(lp))

        for dist in sorted(self.objs(ds, DCAT.distribution), key=str):
            self._distribution(dist)
        if not b.source_url and isinstance(ds, URIRef):
            b.source_url = str(ds)

        # Keep unmapped properties: dataset-level as '<prefix:name>', distribution-level as 'distribution.<prefix:name>'.
        leftovers: dict[str, list[str]] = {}
        nodes = [("dataset", "", ds)] + [("distribution", "distribution.", d) for d in self.objs(ds, DCAT.distribution, role="x")]
        for role, prefix, node in nodes:
            for p in sorted(set(g.predicates(node, None)), key=str):
                if (role, p) in self.used or p == RDF.type:
                    continue
                values = leftovers.setdefault(prefix + self.qname(p), [])
                for o in sorted(g.objects(node, p), key=lambda n: _node_key(g, n)):
                    v = self.describe(o)
                    if v and v not in values:
                        values.append(v)
        b.retain(leftovers)

    def _distribution(self, d):
        role = "distribution"
        download = self.objs(d, DCAT.downloadURL, role=role)
        access = self.objs(d, DCAT.accessURL, role=role)
        url = str((download or access or [""])[0])
        fmt_node = (self.objs(d, DCTERMS.format, role=role) or [None])[0]
        media = (self.objs(d, DCAT.mediaType, role=role) or [None])[0]
        fmt = _local(fmt_node) if isinstance(fmt_node, URIRef) else (self.name_of(fmt_node) if fmt_node is not None else None)
        mimetype = _local_mime(media) if media is not None else None
        title = self.lit(d, DCTERMS.title, role=role)
        desc = self.lit(d, DCTERMS.description, role=role)
        lic = [str(n) if isinstance(n, URIRef) else self.name_of(n) for n in self.objs(d, DCTERMS.license, role=role)]
        lic = [x for x in lic if x]
        for value in lic:
            self.b.add_license(value)
        self.b.add_resource(
            url, title or desc, format=fmt.upper() if fmt and len(fmt) <= 10 else fmt, description=desc if title else None,
            mimetype=mimetype,
            access_url=str(access[0]) if download and access and access[0] != download[0] else None,
            size=self.lit(d, DCAT.byteSize, role=role),
            license=lic[0] if lic else None,
            issued=self.lit(d, DCTERMS.issued, role=role),
            modified=self.lit(d, DCTERMS.modified, role=role),
            distribution_uri=str(d) if isinstance(d, URIRef) else None,
        )
        self.objs(d, DCTERMS.identifier, DCTERMS.rights, DCAT.accessService, role=role)

    def _spatial(self, node):
        b = self.b
        geoms = 0
        for p in (LOCN.geometry, DCAT.bbox, DCAT.centroid, GSP.asWKT):
            for v in sorted(self.g.objects(node, p), key=str):
                self.used.add(("dataset", DCTERMS.spatial))
                geom = _parse_geometry(str(v))
                if geom:
                    b.add_geometry(geom)
                    geoms += 1
                    break
            if geoms:
                break
        label = _best_literal([o for p in (SKOS.prefLabel, RDFS.label, LOCN.geographicName, DCTERMS.title)
                               for o in sorted(self.g.objects(node, p), key=str)], self.lang)
        if label:
            b.add_extra("spatial_place", label)
        if isinstance(node, URIRef):
            b.add_extra("spatial_uri", str(node))
        elif isinstance(node, Literal) and not geoms:
            geom = _parse_geometry(str(node))
            if geom:
                b.add_geometry(geom)
            else:
                b.add_extra("spatial_place", str(node))

    def _temporal(self, node):
        if isinstance(node, Literal):
            rng = split_interval(str(node))
            if rng:
                self.b.set_temporal(*rng)
            return
        start = _first(self.g, node, DCAT.startDate, SCHEMA.startDate, SDO.startDate)
        end = _first(self.g, node, DCAT.endDate, SCHEMA.endDate, SDO.endDate)
        if start is None and end is None:
            for p, attr in ((TIME.hasBeginning, "start"), (TIME.hasEnd, "end")):
                for inst in sorted(self.g.objects(node, p), key=lambda n: _node_key(self.g, n)):
                    v = _first(self.g, inst, TIME.inXSDDateTimeStamp, TIME.inXSDDateTime, TIME.inXSDDate)
                    if attr == "start":
                        start = v
                    else:
                        end = v
        self.b.set_temporal(start, end)


def _node_key(g, node, depth: int = 0) -> tuple:
    """Deterministic sort key; blank nodes (random ids per parse) are keyed by their content."""
    if isinstance(node, URIRef):
        return (0, str(node))
    if isinstance(node, Literal):
        return (1, node.language or "", str(node))
    if depth > 2:
        return (2,)
    return (2, tuple(sorted((str(p), _node_key(g, o, depth + 1)) for p, o in g.predicate_objects(node))))


def _first(g, node, *preds):
    for p in preds:
        for o in sorted(g.objects(node, p), key=str):
            return str(o)
    return None


def _best_literal(values, lang: str | None) -> str | None:
    """Prefer the chosen language, then untagged, then English, then any (skip machine translations)."""
    lits = [v for v in values if isinstance(v, Literal)]
    if not lits:
        return str(values[0]) if values else None
    for want in (lang, None, "en"):
        for v in lits:
            if v.language == want:
                return str(v)
    human = [v for v in lits if not (v.language or "").count("-t-")]
    return str((human or lits)[0])


def _local(node) -> str:
    s = str(node).rstrip("/")
    return re.split(r"[/#]", s)[-1]


def _local_mime(node) -> str:
    s = str(node)
    m = re.search(r"iana\.org/assignments/media-types/(.+)$", s)
    return m.group(1) if m else s


_WKT_RE = re.compile(r"^\s*(?:<[^>]+>\s*)?(POINT|POLYGON|MULTIPOLYGON|ENVELOPE)\s*\((.*)\)\s*$", re.I | re.S)


def _parse_geometry(text: str) -> dict | None:
    """GeoJSON string or WKT (POINT, POLYGON, MULTIPOLYGON, ENVELOPE) -> GeoJSON geometry."""
    t = text.strip()
    if t.startswith("{"):
        try:
            g = json.loads(t)
            return g if isinstance(g, dict) and "type" in g else None
        except ValueError:
            return None
    m = _WKT_RE.match(t)
    if not m:
        return None
    kind, body = m.group(1).upper(), m.group(2)

    def ring(s):
        return [[float(x) for x in pt.split()[:2]] for pt in s.strip().strip("()").split(",")]

    try:
        if kind == "POINT":
            x, y = (float(v) for v in body.split()[:2])
            return {"type": "Point", "coordinates": [x, y]}
        if kind == "ENVELOPE":  # ENVELOPE(minX, maxX, maxY, minY)
            w, e, n, s = (float(v) for v in body.split(","))
            return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}
        if kind == "POLYGON":
            return {"type": "Polygon", "coordinates": [ring(r) for r in re.findall(r"\(([^()]+)\)", body)]}
        if kind == "MULTIPOLYGON":
            polys = re.findall(r"\(\s*(\([^()]+\)(?:\s*,\s*\([^()]+\))*)\s*\)", body)
            return {"type": "MultiPolygon",
                    "coordinates": [[ring(r) for r in re.findall(r"\(([^()]+)\)", p)] for p in polys]}
    except ValueError:
        return None
    return None


# ---------------------------------------------------------------------------
# DCAT-US (Project Open Data) JSON / compacted DCAT JSON without inline context
# ---------------------------------------------------------------------------

_JSON_IGNORE = {"@type", "@context", "@id", "conformsTo", "describedBy", "distribution[].@type", "publisher.@type",
                "contactPoint.@type"}


def _strip_prefixes(v):
    if isinstance(v, dict):
        return {re.sub(r"^(dcat|dct|dcterms|foaf|vcard|adms):", "", k): _strip_prefixes(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_strip_prefixes(x) for x in v]
    return v


def _convert_json(data, b: CkanBuilder):
    if isinstance(data, dict) and isinstance(data.get("dataset"), list):  # whole catalog
        if len(data["dataset"]) > 1:
            b.warn(f"catalog contains {len(data['dataset'])} datasets; converted only the first")
        data = data["dataset"][0]
    data = _strip_prefixes(data)
    t = Tracker(_JSON_IGNORE)
    d = TJ(data, t)

    b.external_id = pick_identifier([d.get("identifier"), d.get("@id"), d.get("landingPage")])
    doi = extract_doi(b.external_id)
    if doi:
        b.add_extra("doi", doi)
    b.set_title(d.get("title"))
    b.set_notes(d.get("description"))
    b.add_tags([k for k in tj_list(d.get("keyword"))])
    for th in tj_list(d.get("theme")):
        b.add_extra("theme", th)
    pub = d.get("publisher")
    if pub is not None:
        b.set_publisher(pub.get("name") if isinstance(pub, TJ) else pub)
        if isinstance(pub, TJ) and pub.get("subOrganizationOf"):
            b.add_extra("publisher_parent", pub.get("subOrganizationOf").raw)
    for cp in tj_list(d.get("contactPoint")):
        if isinstance(cp, TJ):
            email = cp.get("hasEmail") or ""
            b.add_contact(cp.get("fn"), email=email[7:] if email.startswith("mailto:") else email or None)
    b.set_issued(d.get("issued"))
    b.set_modified(d.get("modified"))
    described = d.get("describedBy")
    b.add_docs_url(described)
    for key, extra in (("accessLevel", "access_level"),
                       ("rights", "access_rights"), ("accrualPeriodicity", "frequency"), ("language", "language"),
                       ("bureauCode", "bureau_code"), ("programCode", "program_code"), ("references", "references"),
                       ("isPartOf", "is_part_of"), ("dataQuality", "data_quality"), ("describedByType", "data_dictionary_type"),
                       ("systemOfRecords", "system_of_records"), ("primaryITInvestmentUII", "primary_it_investment_uii")):
        v = d.get(key)
        b.add_extra(extra, v.raw if isinstance(v, TJ) else v)
    b.add_license(d.get("license"))
    b.set_field("version", d.get("version"))

    sp = d.get("spatial")
    if isinstance(sp, str):
        nums = re.split(r"\s*,\s*", sp.strip())
        if len(nums) == 4 and all(re.fullmatch(r"-?\d+(\.\d+)?", n) for n in nums):
            b.add_bbox(*nums)  # DCAT-US bbox: "west,south,east,north"
        else:
            geom = _parse_geometry(sp)
            if geom:
                b.add_geometry(geom)
            else:
                b.add_extra("spatial_place", sp)
    elif isinstance(sp, TJ):
        b.add_geometry(sp.raw)
    tmp = d.get("temporal")
    if isinstance(tmp, str):
        rng = split_interval(tmp)
        b.set_temporal(*(rng or (tmp, tmp)))

    lp = d.get("landingPage")
    if lp:
        b.source_url = lp
        b.set_landing_page(lp)
    for dist in tj_list(d.get("distribution")):
        url = dist.get("downloadURL") or dist.get("accessURL") or ""
        b.add_resource(url, dist.get("title") or dist.get("description"),
                       format=dist.get("format"), mimetype=dist.get("mediaType"),
                       description=dist.get("description") if dist.get("title") else None,
                       access_url=dist.get("accessURL") if dist.get("downloadURL") and dist.get("accessURL") != dist.get("downloadURL") else None,
                       conforms_to=dist.get("conformsTo"), described_by=dist.get("describedBy"))
    if not b.source_url and is_url(b.external_id or ""):
        b.source_url = b.external_id
    b.retain(t.leftovers(json_leaf_items(data)))
