"""STAC Collection / Item / Catalog (JSON) -> CKAN."""

from __future__ import annotations

from ..ckan import CkanBuilder
from ..util import TJ, Tracker, extract_doi, json_leaf_items, tj_list, unwrap

# Structural/housekeeping keys that carry no dataset metadata.
IGNORE = {"type", "stac_version", "links[].type", "links[].method", "links[].body", "links[].headers", "links[].merge"}

# Asset roles that are not data downloads (still kept, but described as such).
_AUX_ROLES = {"thumbnail", "overview", "metadata"}


def convert(parsed, b: CkanBuilder):
    data = parsed.data
    if data.get("type") == "FeatureCollection":
        feats = data.get("features") or []
        if len(feats) > 1:
            b.warn(f"FeatureCollection has {len(feats)} items; converted only the first")
        data = feats[0]
    t = Tracker(IGNORE)
    d = TJ(data, t)
    kind = data.get("type")
    if kind == "Feature":
        _item(d, b)
    else:
        _collection(d, b, is_catalog=(kind == "Catalog"))
    _common(d, b)
    b.retain(t.leftovers(json_leaf_items(data)))


def _collection(d: TJ, b: CkanBuilder, is_catalog: bool):
    b.external_id = d.get("id")
    b.set_title(d.get("title") or d.get("id"))
    b.set_notes(d.get("description"))
    b.add_tags(list(tj_list(d.get("keywords"))))
    lic = d.get("license")
    # "proprietary"/"various"/"other" mean "not an SPDX id": the license is in links[rel=license].
    if lic and lic not in ("proprietary", "various", "other"):
        b.add_license(lic)
    elif lic:
        b.add_extra("license_type", lic)

    extent = d.get("extent")
    if extent:
        bboxes = extent.get("spatial", {}).get("bbox") if extent.get("spatial") else None
        if bboxes:
            # Per spec, the first bbox is the overall extent; the rest are sub-extents.
            _add_bbox(b, bboxes[0].raw)
            if len(bboxes) > 1:
                b.add_extra("spatial_sub_extents", bboxes.raw[1:])
        intervals = extent.get("temporal", {}).get("interval") if extent.get("temporal") else None
        if intervals:
            start, end = intervals.raw[0]
            b.set_temporal(start, end)

    for p in tj_list(d.get("providers")):
        roles = list(tj_list(p.get("roles")))
        if {"producer", "licensor"} & set(roles) or not roles:
            b.add_creator(p.get("name"), identifier=p.get("url"), role=",".join(roles) or None)
        if "host" in roles:
            b.add_contact(p.get("name"), role="host", website=p.get("url"))
            b.set_publisher(p.get("name"), website=p.get("url"))
    if d.get("providers"):
        b.add_extra("providers", d.get("providers").raw)

    summaries = d.get("summaries")
    if summaries:
        gsd = [g for g in tj_list(summaries.get("gsd")) if isinstance(g, (int, float))] if isinstance(summaries.get("gsd"), TJ) else []
        if gsd:
            b.set_spatial_resolution(min(gsd), "meter")
        for key in ("platform", "constellation", "instruments"):
            v = summaries.get(key)
            b.add_extra(f"stac:{key}", list(tj_list(v)) if v is not None else None)
        b.add_extra("stac_summaries", summaries.raw)
    b.set_issued(d.get("created"))
    b.set_modified(d.get("updated"))
    if d.get("item_assets"):
        b.add_extra("stac_item_assets", sorted(d.get("item_assets").raw))
    if is_catalog:
        b.add_extra("stac_type", "Catalog")
    else:
        b.add_extra("stac_type", "Collection")


def _item(d: TJ, b: CkanBuilder):
    coll = d.get("collection")
    item_id = d.get("id")
    b.external_id = f"{coll}/{item_id}" if coll else item_id
    props = d.get("properties") or TJ({}, Tracker())
    b.set_title(props.get("title") or item_id)
    b.set_notes(props.get("description"))
    b.add_tags(list(tj_list(props.get("keywords"))))
    if props.get("license"):
        b.add_license(props.get("license"))
    bbox = d.get("bbox")
    if d.get("geometry"):
        b.add_geometry(d.get("geometry").raw)
        b.add_extra("bbox", bbox.raw if bbox else None)
    elif bbox:
        _add_bbox(b, bbox.raw)
    start = props.get("start_datetime") or props.get("datetime")
    end = props.get("end_datetime") or props.get("datetime")
    b.set_temporal(start, end)
    for p in tj_list(props.get("providers")):
        b.add_creator(p.get("name"), identifier=p.get("url"), role=",".join(tj_list(p.get("roles"))) or None)
    handled = {"title", "description", "keywords", "license", "start_datetime", "end_datetime", "datetime", "providers"}
    b.set_issued(props.get("created"))
    b.set_modified(props.get("updated"))
    b.set_spatial_resolution(props.get("gsd"), "meter")
    for key in props.keys():
        if key in handled:
            continue
        v = props.get(key)
        b.add_extra(f"stac:{key}" if ":" not in key else key, v.raw if isinstance(v, TJ) else v)
    b.add_extra("stac_type", "Item")
    b.add_extra("stac_collection", coll)


def _common(d: TJ, b: CkanBuilder):
    for name in d.get("assets", {}).keys() if d.get("assets") else []:
        a = d.get("assets").get(name)
        roles = list(tj_list(a.get("roles")))
        # Remaining asset fields (eo:bands, gsd, raster:bands, ...) ride along on the resource.
        known = {"href", "title", "type", "description", "roles"}
        rest = {k: unwrap(a.get(k)) for k in a.keys() if k not in known}
        res = b.add_resource(
            a.get("href"),
            a.get("title") or name,
            mimetype=a.get("type"),
            description=a.get("description"),
            stac_asset_key=name,
            roles=",".join(roles) if roles else None,
            **rest,
        )
        if set(roles) & _AUX_ROLES and "data" not in roles:
            res.setdefault("resource_type", "auxiliary")
    for link in tj_list(d.get("links")):
        rel, href = link.get("rel"), link.get("href")
        link.get("title")  # display text only
        if rel == "self":
            b.source_url = href
        elif rel == "license":
            b.add_license(href)
        elif rel in ("items",):
            b.add_resource(href, "STAC items endpoint", format="STAC API", description=link.get("title"))
        elif rel in ("describedby", "about", "license-info") and link.get("type") in (None, "text/html"):
            b.add_docs_url(href)
            b.landing_page = b.landing_page or href  # human-readable dataset page
            b.add_extra(f"link_{rel}", href)
        elif rel == "cite-as":
            doi = extract_doi(href)
            b.add_extra("doi", doi) if doi else b.add_extra("link_cite_as", href)
        elif rel in ("describedby", "about", "via", "alternate", "preview", "related"):
            b.add_extra(f"link_{rel}", href)
        elif rel in ("root", "parent", "collection", "child", "item"):
            b.add_extra(f"stac_{rel}", href)
        else:
            b.add_extra("related_links", f"{rel}: {href}")
    b.set_resource_type("dataset")
    if d.get("sci:doi"):
        b.add_extra("doi", extract_doi(d.get("sci:doi")) or d.get("sci:doi"))
    if d.get("sci:citation"):
        b.add_extra("citation", d.get("sci:citation"))
    if d.get("stac_extensions"):
        b.add_extra("stac_extensions", d.get("stac_extensions").raw)
    b.add_extra("stac_version", d.get("stac_version"))


def _add_bbox(b: CkanBuilder, bbox):
    if len(bbox) == 6:  # 3D: [w, s, zmin, e, n, zmax]
        bbox = [bbox[0], bbox[1], bbox[3], bbox[4]]
    if len(bbox) == 4:
        b.add_bbox(*bbox)
    else:
        b.warn(f"spatial: unexpected bbox {bbox}")
