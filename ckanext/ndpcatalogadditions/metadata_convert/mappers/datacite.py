"""DataCite (REST API JSON, DataCite JSON, or kernel-4 XML) -> CKAN.

XML is first converted to the same dict shape as the REST API `attributes`, so one
mapping serves both.
"""

from __future__ import annotations

import re

from lxml import etree

from ..ckan import CkanBuilder
from ..util import TJ, Tracker, extract_doi, is_url, json_leaf_items, split_interval, tj_list, unwrap

# REST API bookkeeping / statistics: not dataset metadata.
IGNORE = {
    "xml", "prefix", "suffix", "metadataVersion", "schemaVersion", "source", "isActive", "state", "reason",
    "viewCount", "viewsOverTime", "downloadCount", "downloadsOverTime", "referenceCount", "citationCount",
    "citationsOverTime", "partCount", "partOfCount", "versionCount", "versionOfCount", "created", "registered",
    "types.ris", "types.bibtex", "types.citeproc", "types.schemaOrg", "landingPage", "agency",
}

# relatedIdentifier relation types whose targets are (parts of) the data itself.
_RESOURCE_RELATIONS = {"HasPart", "IsSupplementedBy", "IsSourceOf", "IsDerivedFrom"}

_DESCRIPTION_EXTRAS = {
    "Methods": "methods", "SeriesInformation": "series_information", "TableOfContents": "table_of_contents",
    "TechnicalInfo": "technical_info", "Other": "other_description",
}


def convert(parsed, b: CkanBuilder):
    if parsed.kind == "xml":
        attrs = xml_to_attributes(parsed.data)
    else:
        data = parsed.data
        attrs = data["data"]["attributes"] if isinstance(data.get("data"), dict) else data
    t = Tracker(IGNORE)
    a = TJ(attrs, t)
    _map(a, b)
    b.retain(t.leftovers(json_leaf_items(attrs)))


def _map(a: TJ, b: CkanBuilder):
    doi = extract_doi(a.get("doi") or "") or _doi_from_identifiers(a)
    b.external_id = f"doi:{doi}" if doi else a.get("id")
    if doi:
        b.source_url = f"https://doi.org/{doi}"
        b.add_extra("doi", doi)

    # titles
    titles = list(tj_list(a.get("titles")))
    main = [x for x in titles if not x.get("titleType")] or titles
    for x in main[:1]:
        b.set_title(x.get("title"))
    for x in titles:
        if x is main[0]:
            continue
        key = {"Subtitle": "subtitle", "TranslatedTitle": "translated_title"}.get(x.get("titleType"), "alternate_title")
        b.add_extra(key, x.get("title"))
        x.get("lang")

    # descriptions
    descs = list(tj_list(a.get("descriptions")))
    abstracts = [x for x in descs if x.get("descriptionType") in (None, "Abstract")]
    for x in abstracts:
        b.set_notes(x.get("description"))
    for x in descs:
        dtype = x.get("descriptionType")
        if dtype and dtype != "Abstract":
            b.add_extra(_DESCRIPTION_EXTRAS.get(dtype, f"description_{dtype.lower()}"), x.get("description"))
        x.get("lang")

    # people
    for c in tj_list(a.get("creators")):
        b.add_creator(_person_name(c), affiliation=_affiliations(c), identifier=_name_id(c))
    contributors = []
    for c in tj_list(a.get("contributors")):
        ctype = c.get("contributorType")
        name = _person_name(c)
        if ctype == "ContactPerson":
            b.add_contact(name, affiliation=_affiliations(c), role=ctype)
        else:
            contributors.append({k: v for k, v in {
                "name": name, "type": ctype, "affiliation": _affiliations(c), "identifier": _name_id(c)}.items() if v})
    b.add_extra("contributors", contributors)

    pub = a.get("publisher")
    if isinstance(pub, TJ):  # DataCite 4.5 publisher object
        ident = pub.get("publisherIdentifier")
        b.set_publisher(pub.get("name"), website=ident if is_url(ident or "") else None)
        pub.get("publisherIdentifierScheme"), pub.get("schemeUri"), pub.get("lang")
    else:
        b.set_publisher(pub)
    b.add_extra("publication_year", a.get("publicationYear"))
    b.set_field("version", a.get("version"))
    b.add_extra("language", a.get("language"))

    types = a.get("types")
    if types:
        general, specific = types.get("resourceTypeGeneral"), types.get("resourceType")
        b.set_resource_type(general or specific)
        if specific and specific != general:
            b.add_extra("resource_type_detail", specific)

    # subjects -> tags (plus scheme-coded subjects kept in extras)
    coded = []
    for s in tj_list(a.get("subjects")):
        b.add_tag(s.get("subject"))
        if s.get("subjectScheme") or s.get("classificationCode"):
            coded.append({k: v for k, v in {
                "subject": s.get("subject"), "scheme": s.get("subjectScheme"),
                "code": s.get("classificationCode"), "uri": s.get("valueUri")}.items() if v})
        s.get("schemeUri"), s.get("lang")
    b.add_extra("subject_classifications", coded)

    # dates
    for dt in tj_list(a.get("dates")):
        dtype, value = dt.get("dateType"), dt.get("date")
        if dtype == "Collected":
            rng = split_interval(value)
            b.set_temporal(*(rng if rng else (value, value)))
        elif dtype == "Issued":
            b.set_issued(value)
        elif dtype == "Updated":
            b.set_modified(value)
        if dtype not in ("Issued", "Updated"):
            b.add_extra(f"date_{(dtype or 'other').lower()}", value)
        b.add_extra(f"date_{(dtype or 'other').lower()}_info", dt.get("dateInformation"))
    b.set_issued(a.get("published") or a.get("publicationYear"))
    b.set_modified(a.get("updated"))  # DataCite record update time, if no Updated date
    b.add_extra("source_metadata_modified", a.get("updated"))

    # identifiers / relations
    alt = []
    for i in list(tj_list(a.get("identifiers"))) + list(tj_list(a.get("alternateIdentifiers"))):
        value = i.get("identifier") or i.get("alternateIdentifier")
        itype = i.get("identifierType") or i.get("alternateIdentifierType")
        if value and extract_doi(value) != doi:
            alt.append({"identifier": value, "type": itype})
    b.add_extra("alternate_identifiers", alt)
    related = []
    for r in tj_list(a.get("relatedIdentifiers")):
        rel, rid, rtype = r.get("relationType"), r.get("relatedIdentifier"), r.get("relatedIdentifierType")
        related.append({k: v for k, v in {"relation": rel, "identifier": rid, "type": rtype,
                                          "resource_type": r.get("resourceTypeGeneral")}.items() if v})
        if rel in _RESOURCE_RELATIONS:
            url = f"https://doi.org/{extract_doi(rid)}" if rtype == "DOI" and extract_doi(rid) else rid
            if is_url(url):
                b.add_resource(url, f"{rel}: {rid}", description=f"Related resource ({rel})")
    b.add_extra("related_identifiers", related)
    if a.get("relatedItems"):
        b.add_extra("related_items", a.get("relatedItems").raw)
    container = a.get("container")
    if container:
        b.add_extra("container", container.raw)

    b.add_extra("sizes", list(tj_list(a.get("sizes"))))
    formats = list(tj_list(a.get("formats")))
    b.add_extra("formats", formats)

    # rights
    for r in tj_list(a.get("rightsList")):
        b.add_license(r.get("rightsIdentifier") or r.get("rightsUri") or r.get("rights"))
        if r.get("rightsUri"):
            b.add_license(r.get("rightsUri"))
        if r.get("rights"):
            b.add_license(r.get("rights"))
        r.get("schemeUri"), r.get("rightsIdentifierScheme"), r.get("lang")

    # geo
    for g in tj_list(a.get("geoLocations")):
        box = g.get("geoLocationBox")
        if box:
            b.add_bbox(box.get("westBoundLongitude"), box.get("southBoundLatitude"),
                       box.get("eastBoundLongitude"), box.get("northBoundLatitude"))
        pt = g.get("geoLocationPoint")
        if pt:
            b.add_point(pt.get("pointLongitude"), pt.get("pointLatitude"))
        poly = g.get("geoLocationPolygon")
        if poly is not None:
            for ring in _polygon_rings(unwrap(poly)):
                b.add_geometry({"type": "Polygon", "coordinates": [ring]})
        b.add_extra("spatial_place", g.get("geoLocationPlace"))

    # funding
    funding = []
    for f in tj_list(a.get("fundingReferences")):
        funding.append({k: v for k, v in {
            "funder": f.get("funderName"), "funder_id": f.get("funderIdentifier"),
            "award_number": f.get("awardNumber"), "award_title": f.get("awardTitle"),
            "award_uri": f.get("awardUri")}.items() if v})
        f.get("funderIdentifierType")
    b.add_extra("funding", funding)

    # resources
    for url in tj_list(a.get("contentUrl")):
        b.add_resource(url, None, format=formats[0] if len(formats) == 1 and "/" not in formats[0] else None)
    landing = a.get("url")
    if landing:
        b.set_landing_page(landing)


def _polygon_rings(value) -> list[list[list[float]]]:
    """Rings from either shape DataCite uses for geoLocationPolygon.

    REST API JSON: [{"polygonPoint": {...}}, ..., {"inPolygonPoint": {...}}] (or a list of such lists)
    XML-derived:   [{"polygonPoints": [{"pointLongitude": .., "pointLatitude": ..}, ...]}]
    """
    polys = []
    items = value if isinstance(value, list) else [value]
    if items and all(isinstance(i, dict) and ("polygonPoint" in i or "inPolygonPoint" in i) for i in items):
        polys = [[i["polygonPoint"] for i in items if "polygonPoint" in i]]
    else:
        for item in items:
            if isinstance(item, dict) and "polygonPoints" in item:
                polys.append(item["polygonPoints"])
            elif isinstance(item, list):
                polys.extend(_polygon_rings_raw(item))
    rings = []
    for pts in polys:
        try:
            ring = [[float(p["pointLongitude"]), float(p["pointLatitude"])] for p in pts]
        except (KeyError, TypeError, ValueError):
            continue
        if len(ring) >= 3:
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            rings.append(ring)
    return rings


def _polygon_rings_raw(items) -> list:
    return [[i["polygonPoint"] for i in items if isinstance(i, dict) and "polygonPoint" in i]]


def _doi_from_identifiers(a: TJ) -> str | None:
    for i in tj_list(a.get("identifiers")):
        if (i.get("identifierType") or "").upper() == "DOI":
            return extract_doi(i.get("identifier"))
    return extract_doi(a.get("id") or "")


def _person_name(c: TJ) -> str | None:
    given, family = c.get("givenName"), c.get("familyName")
    name = c.get("name")
    if given and family and name and given in name and family in name:
        return f"{family}, {given}"
    if name:
        return name
    if family:
        return f"{family}, {given}" if given else family
    return None


def _affiliations(c: TJ) -> str | None:
    names = []
    for af in tj_list(c.get("affiliation")):
        names.append(af.get("name") if isinstance(af, TJ) else af)
    return "; ".join(n for n in names if n) or None


def _name_id(c: TJ) -> str | None:
    for nid in tj_list(c.get("nameIdentifiers")):
        value = nid.get("nameIdentifier")
        scheme = nid.get("nameIdentifierScheme")
        nid.get("schemeUri")
        if value:
            if scheme == "ORCID" and not value.startswith("http"):
                return f"https://orcid.org/{value}"
            return value
    c.get("nameType")
    return None


# ---------------------------------------------------------------------------
# kernel-4 XML -> attributes dict
# ---------------------------------------------------------------------------


def _ln(el) -> str:
    return etree.QName(el).localname


def _text(el) -> str | None:
    if el is None:
        return None
    t = "".join(el.itertext()).strip()
    return t or None


def _attrs(el, *names) -> dict:
    out = {}
    for n in names:
        for k, v in el.attrib.items():
            if etree.QName(k).localname == n:
                out[n] = v
    return out


def _children(el, name):
    return [c for c in el if isinstance(c.tag, str) and _ln(c) == name] if el is not None else []


def _child(el, name):
    c = _children(el, name)
    return c[0] if c else None


def _person(el, kind: str) -> dict:
    name_el = _child(el, f"{kind}Name")
    p = {"name": _text(name_el)}
    if name_el is not None:
        p.update({k: v for k, v in _attrs(name_el, "nameType").items()})
    for f in ("givenName", "familyName"):
        if _child(el, f) is not None:
            p[f] = _text(_child(el, f))
    p["nameIdentifiers"] = [{"nameIdentifier": _text(n), **_attrs(n, "nameIdentifierScheme")}
                            for n in _children(el, "nameIdentifier")]
    p["affiliation"] = [_text(af) for af in _children(el, "affiliation") if _text(af)]
    if kind == "contributor":
        p.update(_attrs(el, "contributorType"))
    return p


def xml_to_attributes(root) -> dict:
    res = root if _ln(root) == "resource" else next(
        (e for e in root.iter() if isinstance(e.tag, str) and _ln(e) == "resource"
         and (etree.QName(e).namespace or "").startswith("http://datacite.org/schema/kernel-")), None)
    if res is None:
        raise ValueError("no DataCite <resource> element found")
    a: dict = {}
    for el in res:
        if not isinstance(el.tag, str):
            continue
        name = _ln(el)
        if name == "identifier":
            itype = _attrs(el, "identifierType").get("identifierType", "DOI")
            if itype.upper() == "DOI":
                a["doi"] = _text(el)
            a.setdefault("identifiers", []).append({"identifier": _text(el), "identifierType": itype})
        elif name == "creators":
            a["creators"] = [_person(c, "creator") for c in _children(el, "creator")]
        elif name == "contributors":
            a["contributors"] = [_person(c, "contributor") for c in _children(el, "contributor")]
        elif name == "titles":
            a["titles"] = [{"title": _text(t), **_attrs(t, "titleType", "lang")} for t in _children(el, "title")]
        elif name == "publisher":
            a["publisher"] = _text(el)
        elif name == "publicationYear":
            a["publicationYear"] = _text(el)
        elif name == "resourceType":
            a["types"] = {"resourceType": _text(el), **_attrs(el, "resourceTypeGeneral")}
        elif name == "subjects":
            a["subjects"] = [{"subject": _text(s), **_attrs(s, "subjectScheme", "schemeURI", "valueURI", "classificationCode", "lang")}
                             for s in _children(el, "subject")]
            for s in a["subjects"]:
                if "valueURI" in s:
                    s["valueUri"] = s.pop("valueURI")
                if "schemeURI" in s:
                    s["schemeUri"] = s.pop("schemeURI")
        elif name == "dates":
            a["dates"] = [{"date": _text(d), **_attrs(d, "dateType", "dateInformation")} for d in _children(el, "date")]
        elif name == "language":
            a["language"] = _text(el)
        elif name == "alternateIdentifiers":
            a["alternateIdentifiers"] = [{"alternateIdentifier": _text(i), **_attrs(i, "alternateIdentifierType")}
                                         for i in _children(el, "alternateIdentifier")]
        elif name == "relatedIdentifiers":
            a["relatedIdentifiers"] = [{"relatedIdentifier": _text(r), **_attrs(r, "relatedIdentifierType", "relationType", "resourceTypeGeneral")}
                                       for r in _children(el, "relatedIdentifier")]
        elif name == "sizes":
            a["sizes"] = [_text(s) for s in _children(el, "size")]
        elif name == "formats":
            a["formats"] = [_text(f) for f in _children(el, "format")]
        elif name == "version":
            a["version"] = _text(el)
        elif name == "rightsList":
            a["rightsList"] = []
            for r in _children(el, "rights"):
                at = _attrs(r, "rightsURI", "rightsIdentifier", "rightsIdentifierScheme", "schemeURI")
                a["rightsList"].append({"rights": _text(r), "rightsUri": at.get("rightsURI"),
                                        "rightsIdentifier": at.get("rightsIdentifier"),
                                        "rightsIdentifierScheme": at.get("rightsIdentifierScheme")})
        elif name == "descriptions":
            a["descriptions"] = [{"description": _desc_text(d), **_attrs(d, "descriptionType", "lang")}
                                 for d in _children(el, "description")]
        elif name == "geoLocations":
            a["geoLocations"] = [_geo(g) for g in _children(el, "geoLocation")]
        elif name == "fundingReferences":
            a["fundingReferences"] = []
            for f in _children(el, "fundingReference"):
                fr = {}
                for c in f:
                    if isinstance(c.tag, str):
                        fr[_ln(c)] = _text(c)
                        if _ln(c) == "awardNumber" and "awardURI" in c.attrib:
                            fr["awardUri"] = c.attrib["awardURI"]
                a["fundingReferences"].append(fr)
        elif name == "relatedItems":
            a["relatedItems"] = [{_ln(c): _text(c) for c in ri if isinstance(c.tag, str)} for ri in el]
        else:
            a.setdefault(name, []).append(_text(el))  # unknown element: retained verbatim
    return a


def _desc_text(el) -> str | None:
    # <br/> elements inside descriptions are line breaks.
    parts = []
    if el.text:
        parts.append(el.text)
    for c in el:
        if isinstance(c.tag, str) and _ln(c) == "br":
            parts.append("\n")
        if c.tail:
            parts.append(c.tail)
    t = "".join(parts).strip()
    return t or None


def _geo(g) -> dict:
    out: dict = {}
    place = _child(g, "geoLocationPlace")
    if place is not None:
        out["geoLocationPlace"] = _text(place)
    pt = _child(g, "geoLocationPoint")
    if pt is not None:
        if len(pt):
            out["geoLocationPoint"] = {_ln(c): _text(c) for c in pt if isinstance(c.tag, str)}
        elif _text(pt):  # kernel-3: "lat lon"
            lat, lon = re.split(r"[\s,]+", _text(pt))[:2]
            out["geoLocationPoint"] = {"pointLatitude": lat, "pointLongitude": lon}
    box = _child(g, "geoLocationBox")
    if box is not None:
        if len(box):
            out["geoLocationBox"] = {_ln(c): _text(c) for c in box if isinstance(c.tag, str)}
        elif _text(box):  # kernel-3: "south west north east"
            s, w, n, e = re.split(r"[\s,]+", _text(box))[:4]
            out["geoLocationBox"] = {"southBoundLatitude": s, "westBoundLongitude": w,
                                     "northBoundLatitude": n, "eastBoundLongitude": e}
    polys = _children(g, "geoLocationPolygon")
    if polys:
        out["geoLocationPolygon"] = [
            {"polygonPoints": [{_ln(c): _text(c) for c in p if isinstance(c.tag, str)} for p in _children(poly, "polygonPoint")]}
            for poly in polys
        ]
    return out
