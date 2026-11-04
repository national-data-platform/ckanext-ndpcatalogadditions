"""ISO 19115 (19139 gmd/gmi XML, and 19115-3 mdb XML) -> CKAN.

XPaths use local-name() so the same code reads both encodings, and gco:CharacterString
vs gmx:Anchor vs gco:CharacterString-for-URL variants.
"""

from __future__ import annotations

import re

from lxml import etree

from ..ckan import CkanBuilder, guess_format
from ..util import extract_doi, is_url

_VALUE_TAGS = ("CharacterString", "Anchor", "URL", "LocalisedCharacterString", "Date", "DateTime",
               "Decimal", "Real", "Integer", "Boolean", "Measure", "Distance", "FileName")

# Responsible-party roles that make someone a creator vs. a contact.
_CREATOR_ROLES = {"author", "originator", "principalInvestigator", "coAuthor", "creator", "owner"}
_CONTACT_ROLES = {"pointOfContact", "custodian", "distributor", "resourceProvider", "publisher"}

_PROTOCOL_FORMATS = [
    (r"wms", "WMS"), (r"wfs", "WFS"), (r"wcs", "WCS"), (r"wmts", "WMTS"), (r"opendap|dods", "OPeNDAP"),
    (r"thredds", "THREDDS"), (r"erddap", "ERDDAP"), (r"ftp", "FTP"), (r"arcgis|esri", "ArcGIS REST"),
]


# Leftover ISO properties not worth an extras entry: encoding details, codes that are meaningless once
# separated from their sibling value (dateType, function, ...), and contact boilerplate (phone, address, hours).
_IGNORE_PROPS = {
    "characterSet", "presentationForm", "dateType", "function", "protocol", "applicationProfile",
    "facsimile", "hoursOfService", "deliveryPoint", "city", "administrativeArea", "postalCode", "country",
}
# Whole subtrees to skip: the keyword thesaurus citation (its title is kept in keyword_thesauri).
_IGNORE_SUBTREES = ("identificationInfo.descriptiveKeywords.thesaurusName",)


def _ln(el) -> str:
    return etree.QName(el).localname


def _x(el, path: str):
    """XPath where each plain 'name' step matches by local name (namespace-agnostic)."""
    steps = [f"*[local-name()='{s}']" if re.fullmatch(r"[A-Za-z_]\w*", s) else s for s in path.split("/")]
    return el.xpath("/".join(steps))


class _Iso:
    def __init__(self, root):
        self.root = root
        self.used: set[str] = set()

    def path_of(self, el) -> str:
        parts = []
        while el is not None and el is not self.root.getparent():
            parts.append(_ln(el))
            el = el.getparent()
        return "/".join(reversed(parts))

    def use(self, el):
        if el is not None:
            self.used.add(self.path_of(el))

    def nodes(self, el, path: str) -> list:
        return [n for n in _x(el, path) if isinstance(n, etree._Element)] if el is not None else []

    def node(self, el, path: str):
        n = self.nodes(el, path)
        return n[0] if n else None

    def val(self, el):
        """Text of a property element: its value child, codeListValue, or own text. Marks it used."""
        if el is None:
            return None
        self.use(el)
        if el.get("codeListValue"):
            return el.get("codeListValue").strip() or None
        for c in el.iter():
            if isinstance(c.tag, str) and _ln(c) in _VALUE_TAGS and c.text and c.text.strip():
                return c.text.strip()
            if isinstance(c.tag, str) and c.get("codeListValue"):
                return c.get("codeListValue").strip()
        if el.get("indeterminatePosition") and not (el.text or "").strip():
            return None
        text = (el.text or "").strip()
        return text or None

    def text(self, el, path: str):
        return self.val(self.node(el, path))

    def texts(self, el, path: str) -> list[str]:
        return [v for v in (self.val(n) for n in self.nodes(el, path)) if v]

    def href(self, el) -> str | None:
        if el is None:
            return None
        for c in el.iter():
            for k, v in c.attrib.items():
                if etree.QName(k).localname == "href" and v.startswith("http"):
                    return v
        return None

    def leftovers(self) -> dict[str, list[str]]:
        """Values of elements no mapping read, keyed by dotted property path.

        The path drops the root and the UpperCamel class / value wrappers, so
        identificationInfo/MD_DataIdentification/supplementalInformation/gco:CharacterString
        becomes 'identificationInfo.supplementalInformation'.
        """
        out: dict[str, list[str]] = {}
        for el in self.root.iter():
            if not isinstance(el.tag, str):
                continue
            value = (el.text or "").strip() or el.get("codeListValue")
            if not value:
                continue
            p = self.path_of(el)
            if any(p == u or p.startswith(u + "/") for u in self.used):
                continue
            props = [s for s in p.split("/")[1:] if s[:1].islower()]
            key = ".".join(props)
            if not props or props[-1] in _IGNORE_PROPS or key.startswith(_IGNORE_SUBTREES):
                continue
            values = out.setdefault(".".join(props), [])
            if value not in values:
                values.append(value)
        return out


def convert(parsed, b: CkanBuilder):
    root = parsed.data
    md = root if _ln(root) in ("MD_Metadata", "MI_Metadata") else next(
        (e for e in root.iter() if isinstance(e.tag, str) and _ln(e) in ("MD_Metadata", "MI_Metadata")), None)
    if md is None:
        raise ValueError("no MD_Metadata / MI_Metadata element found")
    x = _Iso(md)
    _map(x, md, b)
    b.retain(x.leftovers())


def _map(x: _Iso, md, b: CkanBuilder):
    file_id = x.text(md, "fileIdentifier") or x.text(md, "metadataIdentifier/MD_Identifier/code")
    b.external_id = file_id

    ident = x.node(md, "identificationInfo/*[1]")
    if ident is None:
        b.warn("no identificationInfo section")
        return
    cit = x.node(ident, "citation/CI_Citation")

    b.set_title(x.text(cit, "title"))
    for alt in x.texts(cit, "alternateTitle"):
        b.add_extra("alternate_title", alt)
    b.set_notes(x.text(ident, "abstract"))
    b.add_extra("purpose", x.text(ident, "purpose"))
    b.add_extra("supplemental_information", x.text(ident, "supplementalInformation"))
    b.add_extra("frequency", x.text(ident, "resourceMaintenance/MD_MaintenanceInformation/maintenanceAndUpdateFrequency"))
    b.add_extra("status", x.text(ident, "status"))
    b.set_field("version", x.text(cit, "edition"))
    dates = {}
    for cd in x.nodes(cit, "date/CI_Date"):
        dtype = x.text(cd, "dateType") or "date"
        dates.setdefault(dtype, x.text(cd, "date"))
    b.set_issued(dates.get("publication") or dates.get("creation"))
    b.set_modified(dates.get("revision"))
    for dtype, value in dates.items():
        if dtype not in ("publication", "revision"):
            b.add_extra(f"date_{dtype.lower()}", value)

    # Citation identifiers (often a DOI; used as external id when there is no fileIdentifier)
    first_doi = None
    for idn in x.nodes(cit, "identifier/*"):
        code_el = x.node(idn, "code")
        code = x.val(code_el)
        x.text(idn, "authority/CI_Citation/title")
        x.text(idn, "codeSpace")
        doi = extract_doi(code) or extract_doi(x.href(code_el))
        if doi:
            first_doi = first_doi or doi
            b.add_extra("doi", doi)
            b.source_url = b.source_url or f"https://doi.org/{doi}"
        elif code:
            b.add_extra("citation_identifier", code)
    if not b.external_id and first_doi:
        b.external_id = f"doi:{first_doi}"
    b.add_extra("citation", x.text(cit, "otherCitationDetails"))

    # People
    for party in x.nodes(cit, "citedResponsibleParty/*") + x.nodes(ident, "pointOfContact/*") + x.nodes(md, "contact/*"):
        p = _party(x, party)
        role = p.get("role")
        details = {k: p[k] for k in ("position", "phone", "address") if k in p}
        if role in _CREATOR_ROLES:
            b.add_creator(p.get("name") or p.get("organisation"), email=p.get("email"),
                          affiliation=p.get("organisation") if p.get("name") else None, role=role,
                          identifier=p.get("website"), **details)
        elif role in _CONTACT_ROLES or party.getparent() is not None and _ln(party.getparent()) in ("pointOfContact", "contact"):
            b.add_contact(p.get("name") or p.get("organisation"), email=p.get("email"),
                          affiliation=p.get("organisation") if p.get("name") else None, role=role,
                          website=p.get("website"), **details)
        else:
            b.add_extra("other_parties", p)
        if role == "publisher":
            b.set_publisher(p.get("organisation") or p.get("name"), email=p.get("email"), website=p.get("website"))

    # Keywords: theme/discipline/place become tags; every keyword is also kept by type.
    by_type: dict[str, list[str]] = {}
    thesauri: list[str] = []
    for kw in x.nodes(ident, "descriptiveKeywords/MD_Keywords"):
        ktype = x.text(kw, "type") or "theme"
        thesaurus = x.text(kw, "thesaurusName/CI_Citation/title")
        words = x.texts(kw, "keyword")
        if ktype in ("theme", "discipline", "place"):
            b.add_tags(words)
        by_type.setdefault(ktype, []).extend(w for w in words if w not in by_type.get(ktype, []))
        if thesaurus and thesaurus not in thesauri:
            thesauri.append(thesaurus)
    for ktype, words in by_type.items():
        b.add_extra(f"{ktype}_keywords", words)
    b.add_extra("keyword_thesauri", thesauri)
    for tc in x.texts(ident, "topicCategory"):
        b.add_extra("topic_category", tc)

    # Constraints / license
    for c in x.nodes(ident, "resourceConstraints/*"):
        texts = x.texts(c, "useLimitation") + x.texts(c, "otherConstraints") + x.texts(c, "reference//title")
        for t in texts:
            b.add_license(t)
        for n in x.nodes(c, "otherConstraints") + x.nodes(c, "reference//onlineResource//linkage"):
            href = x.href(n) or (x.val(n) if is_url(x.val(n) or "") else None)
            if href:
                b.add_license(href)
        for code in x.texts(c, "accessConstraints") + x.texts(c, "useConstraints"):
            b.add_extra("constraint_codes", code)
        x.texts(c, "classification")

    # Extents
    for ex in x.nodes(ident, "extent/EX_Extent") + x.nodes(ident, "extent/*/EX_Extent"):
        b.add_extra("extent_description", x.text(ex, "description"))
        for bb in x.nodes(ex, "geographicElement/EX_GeographicBoundingBox"):
            b.add_bbox(x.text(bb, "westBoundLongitude"), x.text(bb, "southBoundLatitude"),
                       x.text(bb, "eastBoundLongitude"), x.text(bb, "northBoundLatitude"))
            x.text(bb, "extentTypeCode")
        for gi in x.nodes(ex, "geographicElement/EX_GeographicDescription"):
            b.add_extra("spatial_place", x.text(gi, ".//code"))
        for tp in x.nodes(ex, "temporalElement/*/extent/*"):
            if _ln(tp) == "TimePeriod":
                begin, end = x.node(tp, "beginPosition"), x.node(tp, "endPosition")
                if begin is None:
                    begin, end = x.node(tp, "begin//timePosition"), x.node(tp, "end//timePosition")
                b.set_temporal(x.val(begin), x.val(end))
                if end is not None and end.get("indeterminatePosition"):
                    b.add_extra("temporal_end_indeterminate", end.get("indeterminatePosition"))
            elif _ln(tp) == "TimeInstant":
                v = x.text(tp, "timePosition")
                b.set_temporal(v, v)
            x.text(tp, "description")
        for vx in x.nodes(ex, "verticalElement/EX_VerticalExtent"):
            b.add_extra("vertical_extent", {"min": x.text(vx, "minimumValue"), "max": x.text(vx, "maximumValue")})

    for g in x.nodes(ident, "graphicOverview/*"):
        b.add_extra("graphic_overview", x.text(g, "fileName") or x.href(g))
        x.text(g, "fileDescription"), x.text(g, "fileType")
    for lang in x.texts(ident, "language"):
        b.add_extra("language", lang)
    for srt in x.texts(ident, "spatialRepresentationType"):
        b.add_extra("spatial_representation_type", srt)
    for res in x.nodes(ident, "spatialResolution/*"):
        dist = x.node(res, "distance")
        if dist is not None:
            uom = next((c.get("uom") for c in dist.iter() if isinstance(c.tag, str) and c.get("uom")), None)
            b.set_spatial_resolution(x.val(dist), uom)
        b.add_extra("spatial_scale", f"1:{x.text(res, './/denominator')}" if x.text(res, ".//denominator") else None)
    for agg in x.nodes(ident, "aggregationInfo/MD_AggregateInformation") + x.nodes(ident, "associatedResource/*"):
        title = x.text(agg, "aggregateDataSetName/CI_Citation/title") or x.text(agg, "name/CI_Citation/title")
        code = x.text(agg, "aggregateDataSetIdentifier//code") or x.text(agg, ".//identifier//code")
        assoc = x.text(agg, "associationType")
        init = x.text(agg, "initiativeType")
        b.add_extra("related", {k: v for k, v in {"title": title, "identifier": code, "association": assoc,
                                                    "initiative": init}.items() if v})
        if init in ("project", "program", "campaign"):
            b.add_extra(f"{init}", title or code)

    # Distribution -> resources
    formats = x.texts(md, "distributionInfo//distributionFormat/MD_Format/name") + \
        x.texts(md, "distributionInfo//distributorFormat/MD_Format/name") + \
        x.texts(md, "distributionInfo//formatSpecificationCitation//title")
    b.add_extra("formats", sorted(set(formats)))
    # Transfer-option links only (contact web pages also use CI_OnlineResource).
    for olr in x.nodes(md, "distributionInfo//onLine/CI_OnlineResource"):
        url = x.text(olr, "linkage")
        if not url:
            continue
        protocol = x.text(olr, "protocol")
        profile = x.text(olr, "applicationProfile")
        name, desc, func = x.text(olr, "name"), x.text(olr, "description"), x.text(olr, "function")
        b.add_resource(url, name or desc,
                       format=_protocol_format(" ".join(filter(None, (protocol, profile))), url),
                       description=desc, protocol=protocol, function=func, application_profile=profile)
        label = " ".join(filter(None, (name, desc, profile))).lower()
        if "landing page" in label:
            b.landing_page = b.landing_page or url
        elif func == "information":
            b.add_docs_url(url)
    for d in x.nodes(md, "distributionInfo//distributor/MD_Distributor/distributorContact/*"):
        p = _party(x, d)
        b.add_extra("distributor", p.get("organisation") or p.get("name"))
    for fees in x.texts(md, "distributionInfo//fees"):
        b.add_extra("fees", fees)
    for oi in x.texts(md, "distributionInfo//orderingInstructions"):
        b.add_extra("ordering_instructions", oi)

    # Lineage
    for st in x.texts(md, "dataQualityInfo//lineage/LI_Lineage/statement") + x.texts(md, "resourceLineage/*/statement"):
        b.add_extra("lineage", st)

    # Acquisition (ISO 19115-2)
    for ins in x.nodes(md, "acquisitionInformation//instrument/*"):
        b.add_extra("instruments", x.text(ins, "identifier//code") or x.text(ins, "description"))
    for plat in x.nodes(md, "acquisitionInformation//platform/*"):
        b.add_extra("platforms", x.text(plat, "identifier//code") or x.text(plat, "description"))

    # Metadata record info
    level = x.text(md, "hierarchyLevel") or x.text(md, "metadataScope//resourceScope")
    b.set_resource_type("service" if _ln(ident) == "SV_ServiceIdentification" else level)
    b.add_extra("hierarchy_level_name", x.text(md, "hierarchyLevelName"))
    metadata_date = x.text(md, "dateStamp") or x.text(md, "dateInfo/CI_Date/date")
    b.set_modified(metadata_date)  # metadata record date, when the citation has no revision date
    b.add_extra("metadata_date", metadata_date)
    b.add_extra("metadata_standard_version", x.text(md, "metadataStandardVersion"))
    b.add_extra("metadata_standard", x.text(md, "metadataStandardName") or x.text(md, "metadataStandard//title"))
    b.add_extra("metadata_language", x.text(md, "language"))
    b.add_extra("parent_identifier", x.text(md, "parentIdentifier") or x.text(md, "parentMetadata//code"))
    if not b.external_id:
        b.warn("no fileIdentifier/metadataIdentifier; re-ingest cannot be de-duplicated")


def _party(x: _Iso, party) -> dict:
    """CI_ResponsibleParty (19139) or CI_Responsibility (19115-3) -> dict."""
    name = x.text(party, "individualName") or x.text(party, "party//CI_Individual/name")
    org = x.text(party, "organisationName") or x.text(party, "party/CI_Organisation/name")
    email = x.text(party, ".//electronicMailAddress")
    role = x.text(party, "role")
    position = x.text(party, "positionName") or x.text(party, ".//positionName")
    website = x.text(party, ".//onlineResource/CI_OnlineResource/linkage")
    for path in (".//onlineResource/CI_OnlineResource/name", ".//onlineResource/CI_OnlineResource/description",
                 ".//onlineResource/CI_OnlineResource/protocol", ".//onlineResource/CI_OnlineResource/function",
                 ".//hoursOfService", ".//contactInstructions"):
        x.texts(party, path)  # details of the contact web page / hours: not needed in CKAN
    phone = x.text(party, ".//phone//voice") or x.text(party, ".//phone//number")
    addr = [x.text(party, f".//address//{f}") for f in ("deliveryPoint", "city", "administrativeArea", "postalCode", "country")]
    address = ", ".join(a for a in addr if a)
    return {k: v for k, v in {"name": name, "organisation": org, "email": email, "role": role, "position": position,
                              "website": website, "phone": phone, "address": address}.items() if v}


def _protocol_format(protocol: str | None, url: str) -> str | None:
    """Resource format from the online-resource protocol, then the URL itself."""
    p = (protocol or "").lower()
    for pattern, fmt in _PROTOCOL_FORMATS:
        if re.search(pattern, p):
            return fmt
    for service in ("wms", "wfs", "wcs"):
        if re.search(rf"[/=]{service}\b", url.lower()):
            return service.upper()
    fmt = guess_format(url)
    if fmt:
        return fmt
    if "www:link" in p or p.startswith("http"):
        return "HTML"
    return None
