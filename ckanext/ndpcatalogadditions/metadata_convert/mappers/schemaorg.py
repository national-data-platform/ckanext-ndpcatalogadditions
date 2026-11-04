"""schema.org Dataset (JSON-LD) -> CKAN."""

from __future__ import annotations

import re

from ..ckan import CkanBuilder
from ..util import TJ, Tracker, extract_doi, is_url, json_leaf_items, pick_identifier, split_interval, tj_list

IGNORE = {"@context"}
JSONLD_KEYWORDS = {"@type", "@context", "@language"}

_PREFIX_RE = re.compile(r"^(?:https?://schema\.org/|schema:|sdo:)")


def convert(parsed, b: CkanBuilder):
    node = _dataset_node(parsed.data)
    if node is None:
        raise ValueError("no schema.org Dataset node found in JSON-LD")
    node = _normalize_keys(node)
    t = Tracker(IGNORE, JSONLD_KEYWORDS)
    d = TJ(node, t)
    _map(d, b)
    b.retain(t.leftovers(json_leaf_items(node)))


def _dataset_node(data):
    if isinstance(data, list):
        candidates = data
    elif isinstance(data.get("@graph"), list):
        candidates = data["@graph"]
    else:
        candidates = [data]
    for n in candidates:
        types = n.get("@type") if isinstance(n, dict) else None
        types = types if isinstance(types, list) else [types]
        if any(str(t).rsplit("/", 1)[-1].split(":")[-1] == "Dataset" for t in types if t):
            return n
    return candidates[0] if candidates and isinstance(candidates[0], dict) else None


def _normalize_keys(v):
    """Strip 'schema:' / 'https://schema.org/' prefixes from keys, recursively."""
    if isinstance(v, dict):
        return {_PREFIX_RE.sub("", k): _normalize_keys(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_normalize_keys(x) for x in v]
    return v


def _lit(v):
    """Plain string for a JSON-LD value: scalar, {"@value"}, {"@id"}, {"name"}, {"url"}."""
    if v is None:
        return None
    if isinstance(v, TJ):
        if v.is_list():
            return _lit(v[0]) if len(v) else None
        for key in ("@value", "name", "url", "@id", "value"):
            if v.get(key) is not None:
                return _lit(v.get(key))
        return None
    return str(v)


def _map(d: TJ, b: CkanBuilder):
    ids = [d.get("@id")]
    for i in tj_list(d.get("identifier")):
        ids.append(i.get("value") or i.get("url") or i.get("@id") if isinstance(i, TJ) else i)
        if isinstance(i, TJ):
            i.get("propertyID"), i.get("@type")
    url = d.get("url")
    ids.append(url)
    b.external_id = pick_identifier(ids)
    doi = extract_doi(b.external_id)
    if doi:
        b.add_extra("doi", doi)
    b.source_url = _lit(url) or (f"https://doi.org/{doi}" if doi else None)

    for name in tj_list(d.get("name")):
        b.set_title(_lit(name))
    for alt in tj_list(d.get("alternateName")):
        b.add_extra("alternate_title", _lit(alt))
    b.set_notes(_lit(d.get("description")))
    for v in tj_list(d.get("abstract")):
        b.set_notes(_lit(v))

    # People: creator and author are often duplicates.
    for key in ("creator", "author"):
        for p in _people(d.get(key)):
            b.add_creator(p.get("name"), email=p.get("email"), affiliation=p.get("affiliation"), identifier=p.get("identifier"))
    contributors = list(_people(d.get("contributor")))
    b.add_extra("contributors", contributors)
    for p in _people(d.get("maintainer")):
        b.add_contact(p.get("name"), email=p.get("email"), affiliation=p.get("affiliation"), website=p.get("website"))
    for cp in tj_list(d.get("contactPoint")):
        if isinstance(cp, TJ):
            b.add_contact(_lit(cp.get("name")), email=cp.get("email"), website=_lit(cp.get("url")))
            cp.get("@type"), cp.get("contactType")
        else:
            b.add_contact(cp)
    for p in _people(d.get("publisher")):
        b.set_publisher(p.get("name"), email=p.get("email"), website=p.get("website"))
    providers = [p.get("name") for p in _people(d.get("provider")) if p.get("name")]
    b.add_extra("provider", "; ".join(providers))
    funders = [p.get("name") for p in _people(d.get("funder")) if p.get("name")]
    b.add_extra("funder", "; ".join(funders))
    for f in tj_list(d.get("funding")):
        b.add_extra("funding", f.raw if isinstance(f, TJ) else f)

    # Keywords: list, comma-separated string, or DefinedTerm objects.
    for k in tj_list(d.get("keywords")):
        val = _lit(k)
        if val and isinstance(k, str) and "," in k:
            b.add_tags([x.strip() for x in k.split(",")])
        else:
            b.add_tag(val)

    for lic in tj_list(d.get("license")):
        b.add_license(_lit(lic))
    for key in ("conditionsOfAccess", "isAccessibleForFree", "copyrightHolder", "copyrightYear"):
        v = d.get(key)
        b.add_extra(_snake(key), _lit(v) if isinstance(v, TJ) else v)

    b.set_field("version", _lit(d.get("version")))
    b.set_issued(_lit(d.get("datePublished")) or _lit(d.get("dateCreated")))
    b.set_modified(_lit(d.get("dateModified")))
    b.add_extra("date_created", _lit(d.get("dateCreated")))
    types = [t for t in tj_list(d.get("@type")) if t]
    b.set_resource_type(next((str(t).rsplit("/", 1)[-1].split(":")[-1] for t in types), "Dataset"))
    for key in ("mainEntityOfPage", "subjectOf", "documentation"):
        for v in tj_list(d.get(key)):
            b.add_docs_url(_lit(v) if isinstance(v, TJ) else v)
    for lang in tj_list(d.get("inLanguage")):
        b.add_extra("language", _lit(lang))

    # Coverage
    for sc in tj_list(d.get("spatialCoverage")):
        _spatial(sc, b)
    tc = d.get("temporalCoverage")
    tc = _lit(tc) if isinstance(tc, TJ) else tc
    if tc:
        rng = split_interval(tc)
        if rng:
            b.set_temporal(*rng)
        else:
            b.set_temporal(tc, tc)
        b.add_extra("temporal_coverage_text", tc if not rng else None)

    # Distributions
    for dist in tj_list(d.get("distribution")):
        if not isinstance(dist, TJ):
            b.add_resource(dist)
            continue
        fmt = _lit(dist.get("encodingFormat")) or _lit(dist.get("fileFormat"))
        size = dist.get("contentSize")
        b.add_resource(
            _lit(dist.get("contentUrl")) or _lit(dist.get("url")) or "",
            _lit(dist.get("name")),
            mimetype=fmt if fmt and "/" in fmt else None,
            format=fmt if fmt and "/" not in fmt else None,
            description=_lit(dist.get("description")),
            size=size,
        )
    if b.source_url and is_url(b.source_url):
        b.set_landing_page(b.source_url)

    for key in ("contentSize", "size"):
        b.add_extra("size", d.get(key))
    cat = d.get("includedInDataCatalog")
    if cat:
        b.add_extra("data_catalog", _lit(cat.get("name")) if isinstance(cat, TJ) else cat)
        if isinstance(cat, TJ):
            cat.get("url")
    res = d.get("spatialResolution")
    b.set_spatial_resolution(_lit(res) if isinstance(res, TJ) else res)
    for key in ("sameAs", "citation", "isBasedOn", "isPartOf", "hasPart", "measurementTechnique",
                "variableMeasured", "creativeWorkStatus", "temporalResolution"):
        v = d.get(key)
        if v is not None:
            b.add_extra(_snake(key), v.raw if isinstance(v, TJ) else v)


def _people(value):
    for p in tj_list(value):
        if not isinstance(p, TJ):
            yield {"name": p}
            continue
        name = _lit(p.get("name"))
        given, family = p.get("givenName"), p.get("familyName")
        if given and family:
            name = f"{_lit(family) if isinstance(family, TJ) else family}, {_lit(given) if isinstance(given, TJ) else given}"
        elif not name:
            name = family or given
        affs = [_lit(a) if isinstance(a, TJ) else a for a in tj_list(p.get("affiliation"))]
        ident = p.get("identifier")
        ident = _lit(ident) if isinstance(ident, TJ) else ident
        same = p.get("sameAs")
        same = _lit(same) if isinstance(same, TJ) else same
        node_id = p.get("@id")
        url = p.get("url")
        yield {k: v for k, v in {
            "name": name, "email": p.get("email"),
            "website": _lit(url) if isinstance(url, TJ) else url,
            "affiliation": "; ".join(a for a in affs if a) or None,
            "identifier": ident or node_id or same,
        }.items() if v}


def _spatial(sc, b: CkanBuilder):
    if not isinstance(sc, TJ):
        b.add_extra("spatial_place", sc)
        return
    b.add_extra("spatial_place", _lit(sc.get("name")))
    for geo in tj_list(sc.get("geo")) or [sc]:
        if not isinstance(geo, TJ):
            continue
        box = geo.get("box")
        if box:
            nums = [float(x) for x in re.split(r"[\s,]+", str(_lit(box) if isinstance(box, TJ) else box).strip())]
            if len(nums) == 4:  # schema.org GeoShape box: "south west north east"
                s, w, n, e = nums
                b.add_bbox(w, s, e, n)
            else:
                b.warn(f"spatial: could not parse GeoShape box {box!r}")
        poly = geo.get("polygon")
        if poly:
            nums = [float(x) for x in re.split(r"[\s,]+", str(_lit(poly) if isinstance(poly, TJ) else poly).strip())]
            ring = [[nums[i + 1], nums[i]] for i in range(0, len(nums) - 1, 2)]  # "lat lon" pairs
            if len(ring) >= 3:
                if ring[0] != ring[-1]:
                    ring.append(ring[0])
                b.add_geometry({"type": "Polygon", "coordinates": [ring]})
        lat, lon = geo.get("latitude"), geo.get("longitude")
        if lat is not None and lon is not None:
            b.add_point(lon, lat)
        geo.get("@type")


def _snake(key: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
