"""Dublin Core (oai_dc / simple DC / DC terms XML, or flat DC JSON) -> CKAN."""

from __future__ import annotations

import re

from lxml import etree

from ..ckan import CkanBuilder
from ..detect import NS_DC, NS_DCTERMS
from ..util import Tracker, extract_doi, is_url, pick_identifier

def convert(parsed, b: CkanBuilder):
    if parsed.kind == "xml":
        fields, header = _from_xml(parsed.data)
    else:
        fields, header = _from_json(parsed.data), {}
    t = Tracker()
    _map(fields, header, b, t)
    b.retain({k: fields[k] for k in t.unmapped(sorted(fields))})


def _from_xml(root) -> tuple[dict[str, list[str]], dict]:
    fields: dict[str, list[str]] = {}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        q = etree.QName(el)
        if q.namespace not in (NS_DC, NS_DCTERMS):
            continue
        text = "".join(el.itertext()).strip()
        # RDF-style DC uses rdf:resource attributes instead of text.
        if not text:
            text = next((v for k, v in el.attrib.items() if etree.QName(k).localname == "resource"), "")
        if text:
            fields.setdefault(q.localname, []).append(text)
    header = {}
    for el in root.iter("{*}header"):
        for c in el:
            if isinstance(c.tag, str):
                header.setdefault(etree.QName(c).localname, "".join(c.itertext()).strip())
    return fields, header


def _from_json(data: dict) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for k, v in data.items():
        key = re.split(r"[:.]", k)[-1]
        for item in v if isinstance(v, list) else [v]:
            if isinstance(item, dict):
                item = item.get("value") or item.get("@value") or item.get("name")
            if item not in (None, ""):
                fields.setdefault(key, []).append(str(item))
    return fields


def _map(f: dict[str, list[str]], header: dict, b: CkanBuilder, t: Tracker):
    def get(*keys) -> list[str]:
        out = []
        for k in keys:
            if k in f:
                t.use(k)
                out.extend(v for v in f[k] if v not in out)
        return out

    identifiers = get("identifier")
    b.external_id = pick_identifier(identifiers) or header.get("identifier")
    doi = extract_doi(b.external_id)
    if doi:
        b.add_extra("doi", doi)
        b.source_url = f"https://doi.org/{doi}"
    for i in identifiers:
        if is_url(i):
            b.set_landing_page(i)
            b.source_url = b.source_url or i
        elif not extract_doi(i) and i not in (b.external_id, header.get("identifier")):
            b.add_extra("other_identifiers", i)
    if header.get("identifier"):
        b.add_extra("oai_identifier", header["identifier"])
    if header.get("datestamp"):
        b.add_extra("source_metadata_modified", header["datestamp"])
    for c in get("bibliographicCitation"):
        b.add_extra("citation", c)

    for title in get("title"):
        b.set_title(title)
    for alt in get("alternative"):
        b.add_extra("alternate_title", alt)
    for desc in get("description", "abstract"):
        b.set_notes(desc)
    b.add_extra("table_of_contents", get("tableOfContents"))

    for c in get("creator"):
        b.add_creator(c)
    b.add_extra("contributors", get("contributor"))
    b.set_publisher("; ".join(get("publisher")))
    b.add_tags(get("subject"))

    issued, modified, dates = get("issued"), get("modified"), get("date")
    for v in issued + dates[:1] + get("available") + get("created"):
        b.set_issued(v)
    for v in modified + ([header["datestamp"]] if header.get("datestamp") else []):
        b.set_modified(v)
    for v in dates[1:]:
        b.add_extra("date", v)
    for key in ("created", "available", "dateAccepted", "dateSubmitted", "dateCopyrighted", "valid"):
        for v in get(key):
            b.add_extra(f"date_{_snake(key)}", v)

    for v in get("type"):
        b.set_resource_type(_strip_eurepo(v))
    formats = get("format", "medium")
    b.add_extra("formats", formats)
    b.add_extra("extent", get("extent"))
    for v in get("language"):
        b.add_extra("language", v)
    for v in get("source"):
        b.add_extra("source", v)
    for key in ("relation", "isPartOf", "hasPart", "isVersionOf", "hasVersion", "references", "isReferencedBy",
                "requires", "isRequiredBy", "replaces", "isReplacedBy", "conformsTo", "isFormatOf", "hasFormat"):
        for v in get(key):
            b.add_extra("relation" if key == "relation" else f"relation_{_snake(key)}", v)

    for v in get("rights", "license", "accessRights"):
        if v.startswith("info:eu-repo/semantics/"):
            b.add_extra("access_rights", _strip_eurepo(v))
        else:
            b.add_license(v)
    b.add_extra("rights_holder", get("rightsHolder"))

    for v in get("coverage", "spatial", "temporal"):
        _coverage(v, b)


def _coverage(value: str, b: CkanBuilder):
    """Parse DCMI Box / Point / Period encodings, ISO intervals or plain years; else keep as text."""
    kv = dict(
        (k.strip().lower(), v.strip())
        for k, v in (part.split("=", 1) for part in value.split(";") if "=" in part)
    )
    if {"northlimit", "southlimit", "eastlimit", "westlimit"} <= kv.keys():
        b.add_bbox(kv["westlimit"], kv["southlimit"], kv["eastlimit"], kv["northlimit"])
        b.add_extra("spatial_place", kv.get("name"))
        return
    if {"north", "east"} <= kv.keys():
        b.add_point(kv["east"], kv["north"])
        b.add_extra("spatial_place", kv.get("name"))
        return
    if "start" in kv or "end" in kv:
        b.set_temporal(kv.get("start"), kv.get("end"))
        return
    m = re.fullmatch(r"\s*(\d{4}(?:-\d{2}(?:-\d{2})?)?)\s*(?:/|--|\s-\s|–)\s*(\d{4}(?:-\d{2}(?:-\d{2})?)?)\s*", value)
    if m:
        b.set_temporal(m.group(1), m.group(2))
        return
    b.add_extra("coverage", value)


def _strip_eurepo(v: str) -> str:
    return v.rsplit("/", 1)[-1] if v.startswith("info:eu-repo/semantics/") else v


def _snake(key: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
