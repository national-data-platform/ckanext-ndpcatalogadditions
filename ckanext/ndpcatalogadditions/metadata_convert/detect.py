"""Parse raw input and detect which of the six source schemas it is."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

FORMATS = ("datacite", "dublincore", "schemaorg", "dcat", "iso19115", "stac")

NS_DATACITE = "http://datacite.org/schema/kernel-"
NS_DC = "http://purl.org/dc/elements/1.1/"
NS_DCTERMS = "http://purl.org/dc/terms/"
NS_DCAT = "http://www.w3.org/ns/dcat#"
NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
ISO_NS_HINTS = ("isotc211.org", "standards.iso.org/iso/19115")
ISO_ROOTS = {"MD_Metadata", "MI_Metadata"}

DC_ELEMENTS = {
    "title", "creator", "subject", "description", "publisher", "contributor", "date", "type",
    "format", "identifier", "source", "language", "relation", "coverage", "rights",
}


class FormatError(ValueError):
    pass


@dataclass
class Parsed:
    kind: str  # "json" | "xml" | "rdf"
    data: object  # dict/list | lxml root element | rdf text
    text: str  # original text
    rdf_format: str | None = None  # rdflib parser name for kind == "rdf"


def load(record) -> Parsed:
    """Accept str | bytes | dict | list | Path and return a Parsed record."""
    if isinstance(record, Path):
        record = record.read_bytes()
    if isinstance(record, (dict, list)):
        return Parsed("json", record, json.dumps(record, ensure_ascii=False))
    if isinstance(record, bytes):
        text = record.decode("utf-8-sig", errors="replace")
    elif isinstance(record, str):
        text = record.lstrip("﻿")
    else:
        raise FormatError(f"unsupported input type {type(record).__name__}")
    s = text.lstrip()
    if not s:
        raise FormatError("empty input")
    if s[0] in "{[":
        try:
            return Parsed("json", json.loads(s), text)
        except json.JSONDecodeError as e:
            raise FormatError(f"invalid JSON: {e}") from e
    if s.startswith("<") and not re.match(r"<(https?|urn|_:)", s):
        try:
            parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, remove_comments=True)
            root = etree.fromstring(s.encode("utf-8"), parser=parser)
        except etree.XMLSyntaxError as e:
            raise FormatError(f"invalid XML: {e}") from e
        return Parsed("xml", root, text)
    if re.match(r"(@prefix|@base|PREFIX|BASE|<|_:)", s, re.I):
        return Parsed("rdf", text, text, rdf_format="turtle")
    raise FormatError("input is not JSON, XML or Turtle")


def detect(parsed: Parsed) -> str:
    if parsed.kind == "json":
        return _detect_json(parsed.data)
    if parsed.kind == "xml":
        return _detect_xml(parsed)
    return "dcat"


def _types(node) -> set[str]:
    t = node.get("@type") if isinstance(node, dict) else None
    vals = t if isinstance(t, list) else [t]
    return {str(v).rsplit("/", 1)[-1].rsplit("#", 1)[-1].split(":")[-1] for v in vals if v}


def _detect_json(d) -> str:
    if isinstance(d, list):
        d = d[0] if d else {}
    if not isinstance(d, dict):
        raise FormatError("JSON input is not an object")
    if "stac_version" in d or (d.get("type") in ("Feature", "Collection", "Catalog") and ("assets" in d or "extent" in d or "links" in d)):
        return "stac"
    if d.get("type") == "FeatureCollection" and d.get("features") and "stac_version" in d["features"][0]:
        return "stac"
    attrs = d.get("data", {}).get("attributes") if isinstance(d.get("data"), dict) else None
    if isinstance(attrs, dict) and ("doi" in attrs or "titles" in attrs):
        return "datacite"
    if "titles" in d and ("creators" in d or "doi" in d or "types" in d):
        return "datacite"
    ctx = json.dumps(d.get("@context", "")).lower()
    nodes = d.get("@graph") if isinstance(d.get("@graph"), list) else [d]
    types = set().union(*(_types(n) for n in nodes if isinstance(n, dict)))
    if "dcat" in ctx or any(str(n.get("@type", "")).startswith("dcat:") for n in nodes if isinstance(n, dict)) \
            or "project-open-data" in json.dumps(d.get("conformsTo", "")):
        return "dcat"
    if "schema.org" in ctx or "Dataset" in types or "DataCatalog" in types:
        return "schemaorg"
    keys = {k.split(":")[-1] for k in d}
    if len(keys & DC_ELEMENTS) >= 3 or any(k.startswith(("dc:", "dcterms:", "dc.")) for k in d):
        return "dublincore"
    raise FormatError("could not detect the metadata schema of this JSON record")


def _detect_xml(parsed: Parsed) -> str:
    root = parsed.data
    qn = etree.QName(root)
    ns, local = qn.namespace or "", qn.localname
    # DataCite kernel XML, bare or wrapped (OAI-PMH oai_datacite payload).
    if any((etree.QName(e).namespace or "").startswith(NS_DATACITE) for e in root.iter() if isinstance(e.tag, str)):
        return "datacite"
    if local in ISO_ROOTS or any(_is_iso(e) for e in root.iter() if isinstance(e.tag, str)):
        return "iso19115"
    if ns == NS_RDF:
        if b"dcat" in parsed.text.encode() or NS_DCAT in parsed.text:
            parsed.kind, parsed.data, parsed.rdf_format = "rdf", parsed.text, "xml"
            return "dcat"
        return "dublincore"
    if any(etree.QName(e).namespace in (NS_DC, NS_DCTERMS) for e in root.iter() if isinstance(e.tag, str)):
        return "dublincore"
    raise FormatError(f"could not detect the metadata schema of XML root <{local}>")


def _is_iso(el) -> bool:
    q = etree.QName(el)
    return q.localname in ISO_ROOTS and any(h in (q.namespace or "") for h in ISO_NS_HINTS)
