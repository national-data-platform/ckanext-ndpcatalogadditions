"""CkanBuilder: the shared target model every mapper writes into.

Output conventions (kept from the earlier NDP LLM converter so existing consumers work):
  * extras: list of {"key", "value"} with string values, never a CKAN-reserved key
  * tags: list of {"name", "display_name"}; names lowercase kebab-case
  * spatial: one extras["spatial"] GeoJSON geometry string
  * temporal: extras["temporal"] = '{"startTime": ..., "endTime": ...}' (ISO 8601, Z)
  * URLs live in resources; "url": "" when a resource has no URL
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from .util import clean_text, extract_doi, is_url, normalize_datetime

RESERVED_KEYS = {
    "name", "title", "notes", "owner_org", "license_id", "author", "author_email",
    "maintainer", "maintainer_email", "state", "tags", "resources", "groups", "extras",
    "version", "metadata_created", "metadata_modified", "id",
}
# Not reserved by CKAN core, but kept out of extras by convention / to avoid clashes.
_EXTRA_RENAMES = {
    "license": "rights_statement",
    "url": "source_url",
    "type": "resource_type",
    "private": "source_private",
}

# CKAN core license ids (ckan/model/license.py defaults).
CKAN_LICENSES = {
    "notspecified", "odc-pddl", "odc-odbl", "odc-by", "cc-zero", "cc-by", "cc-by-sa",
    "gfdl", "other-open", "other-pd", "other-at", "uk-ogl", "cc-nc", "other-nc", "other-closed",
}

# (regex over lowercased license id / URL / text) -> CKAN license id. Order matters.
_LICENSE_PATTERNS = [
    (r"(cc0|publicdomain/zero|cc-zero|creative commons zero)", "cc-zero"),
    (r"publicdomain/mark|public domain|public-domain|government-works|us-pd", "other-pd"),
    (r"(by-nc|cc-nc|noncommercial|non-commercial)", "cc-nc"),
    (r"(by-sa|cc-by-sa|sharealike|share-alike)", "cc-by-sa"),
    (r"(by-nd|noderivs)", "other-at"),
    (r"(creativecommons\.org/licenses/by/|cc-by|cc by|creative commons attribution)", "cc-by"),
    (r"(odbl|open database license)", "odc-odbl"),
    (r"(odc-by|opendatacommons\.org/licenses/by)", "odc-by"),
    (r"(pddl|opendatacommons\.org/licenses/pddl)", "odc-pddl"),
    (r"(open-government-licence|open government licence|\bogl\b|uk-ogl)", "uk-ogl"),
    (r"\bgfdl\b|gnu free documentation", "gfdl"),
    (r"(\bmit\b|apache|bsd|\bgpl|lgpl|mpl-|eupl|isc license|unlicense|etalab|dl-de/by)", "other-open"),
    (r"(proprietary|all rights reserved|restricted|closed)", "other-closed"),
]

_MIME_FORMATS = {
    "text/csv": "CSV", "application/json": "JSON", "application/geo+json": "GeoJSON",
    "application/xml": "XML", "text/xml": "XML", "application/pdf": "PDF",
    "application/zip": "ZIP", "application/x-netcdf": "NetCDF", "application/netcdf": "NetCDF",
    "image/tiff": "TIFF", "image/png": "PNG", "image/jpeg": "JPEG", "text/html": "HTML",
    "text/plain": "TXT", "application/vnd.ms-excel": "XLS",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
    "application/x-parquet": "Parquet", "application/vnd.apache.parquet": "Parquet",
    "application/x-hdf5": "HDF5", "application/x-hdf": "HDF",
}
_EXT_FORMATS = {
    "csv": "CSV", "json": "JSON", "geojson": "GeoJSON", "xml": "XML", "pdf": "PDF", "zip": "ZIP",
    "nc": "NetCDF", "nc4": "NetCDF", "tif": "TIFF", "tiff": "TIFF", "png": "PNG", "jpg": "JPEG",
    "jpeg": "JPEG", "txt": "TXT", "xls": "XLS", "xlsx": "XLSX", "parquet": "Parquet",
    "h5": "HDF5", "hdf5": "HDF5", "hdf": "HDF", "shp": "SHP", "gz": "GZ", "tar": "TAR",
    "kml": "KML", "kmz": "KMZ", "html": "HTML", "htm": "HTML", "py": "Python", "r": "R",
    "ipynb": "Jupyter", "tsv": "TSV", "las": "LAS", "laz": "LAZ", "grib": "GRIB", "grib2": "GRIB",
}


# Format -> IANA media type, for resources whose source gives a format but no media type.
_FORMAT_MIMES = {
    "CSV": "text/csv", "TSV": "text/tab-separated-values", "JSON": "application/json",
    "GeoJSON": "application/geo+json", "XML": "application/xml", "GML": "application/gml+xml",
    "PDF": "application/pdf", "ZIP": "application/zip", "GZ": "application/gzip", "TAR": "application/x-tar",
    "NetCDF": "application/x-netcdf", "TIFF": "image/tiff", "COG": "image/tiff", "PNG": "image/png",
    "JPEG": "image/jpeg", "HTML": "text/html", "TXT": "text/plain", "XLS": "application/vnd.ms-excel",
    "XLSX": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "Parquet": "application/vnd.apache.parquet", "HDF5": "application/x-hdf5", "HDF": "application/x-hdf",
    "KML": "application/vnd.google-earth.kml+xml", "KMZ": "application/vnd.google-earth.kmz",
    "Python": "text/x-python", "Jupyter": "application/x-ipynb+json", "STAC API": "application/json",
    "SHP": "application/zip", "GRIB": "application/x-grib",
}

# NDP extras:uploadType values, keyed by lowercase source resource types (DataCite resourceTypeGeneral,
# schema.org @type, ISO hierarchyLevel, DC type, ...).
NDP_UPLOAD_TYPES = ("dataset", "model", "service", "docker-image", "application", "code")
_UPLOAD_TYPE_PATTERNS = [
    (r"software|sourcecode|code|workflow|computationalnotebook", "code"),
    (r"\bmodel", "model"),
    (r"service|webapi|api\b", "service"),
    (r"docker|container image", "docker-image"),
    (r"application|interactiveresource|webapplication", "application"),
]


def guess_mimetype(fmt: str | None) -> str | None:
    if not fmt:
        return None
    if fmt in _FORMAT_MIMES:
        return _FORMAT_MIMES[fmt]
    up = {k.upper(): v for k, v in _FORMAT_MIMES.items()}
    return up.get(fmt.upper())


def map_upload_type(value) -> str:
    s = str(value or "").strip().lower()
    for pattern, kind in _UPLOAD_TYPE_PATTERNS:
        if re.search(pattern, s):
            return kind
    return "dataset"


def _date_value(value) -> str | None:
    """NDP date: keep date-only/partial dates as given, normalize datetimes to UTC 'Z'."""
    s = str(value).strip() if value is not None else ""
    if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", s):
        return s
    return normalize_datetime(s)


def guess_format(url: str | None = None, mimetype: str | None = None) -> str | None:
    if mimetype:
        if "cloud-optimized" in mimetype.lower() or "profile=cloud" in mimetype.lower():
            return "COG"
        mt = mimetype.split(";")[0].strip().lower()
        if mt in _MIME_FORMATS:
            return _MIME_FORMATS[mt]
    if url:
        path = re.split(r"[?#]", url)[0].rstrip("/")
        if path.endswith("/content"):  # Zenodo-style file URLs: .../files/<name>/content
            path = path[: -len("/content")]
        m = re.search(r"\.([A-Za-z0-9]{1,8})$", path.rsplit("/", 1)[-1])
        if m and m.group(1).lower() in _EXT_FORMATS:
            return _EXT_FORMATS[m.group(1).lower()]
    return None


def map_license(value) -> str | None:
    """Map an SPDX id / license URL / free-text license to a CKAN core license id."""
    if not value:
        return None
    s = str(value).strip().lower()
    if s in CKAN_LICENSES:
        return s
    for pattern, lic in _LICENSE_PATTERNS:
        if re.search(pattern, s):
            return lic
    return None


def slugify(text: str, max_len: int = 100) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-_")
    text = re.sub(r"-{2,}", "-", text)
    return text[:max_len].rstrip("-_")


def tag_name(keyword: str) -> str | None:
    """CKAN tag name: lowercase kebab-case, [\\w-. ] only, 2..100 chars."""
    s = re.sub(r"[^\w\-. ]+", " ", keyword.strip().lower(), flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "-", s.strip())
    s = re.sub(r"-{2,}", "-", s).strip("-.")
    s = s[:100].rstrip("-.")
    return s if len(s) >= 2 else None


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


class CkanBuilder:
    def __init__(self, source_format: str):
        self.source_format = source_format
        self.title: str | None = None
        self.notes: str | None = None
        self.fields: dict[str, str] = {}
        self._tags: dict[str, str] = {}
        self._extras: dict[str, list[str]] = {}
        self.resources: list[dict] = []
        self.creators: list[dict] = []
        self.contacts: list[dict] = []
        self._geoms: list[dict] = []
        self._temporal: tuple[str | None, str | None] | None = None
        self._license_values: list[str] = []
        self.publisher: dict[str, str] = {}
        self.issued: str | None = None
        self.modified: str | None = None
        self.resource_type: str | None = None
        self.landing_page: str | None = None
        self.docs_urls: list[str] = []
        self.spatial_resolution: str | None = None
        self.external_id: str | None = None
        self.source_url: str | None = None
        self.warnings: list[str] = []
        self.unmapped: list[str] = []

    # -- simple fields -----------------------------------------------------
    def set_title(self, value):
        value = clean_text(value)
        if value and not self.title:
            self.title = value.replace("\n", " ")
        elif value and value != self.title:
            self.add_extra("alternate_title", value)

    def set_notes(self, value):
        value = clean_text(value)
        if not value:
            return
        self.notes = f"{self.notes}\n\n{value}" if self.notes and value not in self.notes else (self.notes or value)

    def set_field(self, key: str, value):
        if value in (None, ""):
            return
        value = str(value).strip()
        if value and key not in self.fields:
            self.fields[key] = value

    def warn(self, message: str):
        if message not in self.warnings:
            self.warnings.append(message)

    # -- source fields without a CKAN/NDP mapping ---------------------------
    def retain(self, leftovers: dict[str, list[str]]):
        """Keep source fields no mapper used, as extras keyed by their dotted source path.

        `unmapped` then lists exactly these keys, so a dry run shows what was carried over verbatim.
        """
        for path, values in sorted(leftovers.items()):
            key = path if path not in self._extras and path not in RESERVED_KEYS else f"source.{path}"
            self.add_extra(key, values)
            if key in self._extras:
                self.unmapped.append(key)

    # -- NDP dataset-level fields ------------------------------------------
    # First value wins for each setter: mappers call them in order of preference.
    def set_publisher(self, name=None, *, email=None, website=None):
        for key, value in (("name", clean_text(name)), ("email", email), ("website", website)):
            if value and key not in self.publisher:
                if key == "email" and not _EMAIL_RE.match(value):
                    continue
                if key == "website" and not is_url(value):
                    continue
                self.publisher[key] = value.strip()

    def set_issued(self, value):
        if value not in (None, "") and not self.issued:
            self.issued = _date_value(value)

    def set_modified(self, value):
        if value not in (None, "") and not self.modified:
            self.modified = _date_value(value)

    def set_resource_type(self, value):
        """Source resource type (e.g. 'Dataset', 'Software', 'service'); kept and mapped to uploadType."""
        if value not in (None, ""):
            self.add_extra("resource_type", value)
            if not self.resource_type:
                self.resource_type = str(value)

    def set_landing_page(self, url, *, description="Dataset landing page"):
        if not url or not is_url(url):
            return
        self.landing_page = self.landing_page or url
        self.add_resource(url, "Landing page", format="HTML", mimetype="text/html", description=description)

    def add_docs_url(self, url):
        if url and is_url(url) and url not in self.docs_urls:
            self.docs_urls.append(url)

    def set_spatial_resolution(self, value, unit=None):
        """NDP extras:spatialRes must start with a number, e.g. '10 meter'."""
        if value in (None, "") or self.spatial_resolution:
            return
        text = f"{value} {unit}".strip() if unit else str(value).strip()
        if re.match(r"^[+-]?(\d+(\.\d*)?|\.\d+)", text):
            self.spatial_resolution = text
        else:
            self.add_extra("spatial_resolution_text", text)

    # -- tags --------------------------------------------------------------
    def add_tag(self, keyword):
        if keyword is None:
            return
        keyword = clean_text(keyword)
        if not keyword:
            return
        # Hierarchical vocabularies (GCMD "EARTH SCIENCE > OCEANS > ..."): keep the leaf.
        if ">" in keyword:
            keyword = keyword.split(">")[-1].strip()
        display = keyword[:100]
        name = tag_name(keyword)
        if name and re.fullmatch(r"[\d.\-]+", name):  # accession numbers, years: not useful as tags
            return
        if name and name not in self._tags:
            self._tags[name] = display

    def add_tags(self, keywords):
        for k in keywords or []:
            if isinstance(k, str) and ";" in k:
                for part in k.split(";"):
                    self.add_tag(part)
            elif isinstance(k, str) and "," in k and len(k) > 60:
                for part in k.split(","):
                    self.add_tag(part)
            else:
                self.add_tag(k)

    # -- extras ------------------------------------------------------------
    def add_extra(self, key: str, value):
        if value is None or value == "" or value == [] or value == {}:
            return
        key = key.strip()
        if key in RESERVED_KEYS:
            key = f"source_{key}"
        key = _EXTRA_RENAMES.get(key, key)
        if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
            value = "; ".join(v.strip() for v in value if v.strip())
        text = value if isinstance(value, str) else _json(value)
        text = text.strip()
        if not text:
            return
        values = self._extras.setdefault(key, [])
        if text not in values:
            values.append(text)

    # -- people ------------------------------------------------------------
    def add_creator(self, name=None, *, email=None, affiliation=None, identifier=None, role=None, **details):
        person = {k: v for k, v in {
            "name": clean_text(name), "email": email, "affiliation": clean_text(affiliation),
            "identifier": identifier, "role": role, **details,
        }.items() if v}
        if (person.get("name") or person.get("affiliation")) and person not in self.creators:
            self.creators.append(person)

    def add_contact(self, name=None, *, email=None, affiliation=None, role=None, website=None, **details):
        person = {k: v for k, v in {
            "name": clean_text(name), "email": email, "affiliation": clean_text(affiliation), "role": role,
            "website": website if is_url(website or "") else None, **details,
        }.items() if v}
        if person and person not in self.contacts:
            self.contacts.append(person)

    # -- license -----------------------------------------------------------
    def add_license(self, value):
        if value in (None, ""):
            return
        value = str(value).strip()
        if value and value not in self._license_values:
            self._license_values.append(value)

    # -- spatial -----------------------------------------------------------
    def add_bbox(self, west, south, east, north):
        try:
            w, s, e, n = (float(v) for v in (west, south, east, north))
        except (TypeError, ValueError):
            self.warn(f"spatial: could not parse bounding box {[west, south, east, north]}")
            return
        if not (-180 <= w <= 180 and -180 <= e <= 180 and -90 <= s <= 90 and -90 <= n <= 90):
            self.warn(f"spatial: bounding box out of range {[w, s, e, n]}")
            return
        if s > n:
            s, n = n, s
        if w == e and s == n:
            self.add_point(w, s)
            return
        if w > e:  # crosses the antimeridian: split in two
            self._geoms.append(_box(w, s, 180.0, n))
            self._geoms.append(_box(-180.0, s, e, n))
            return
        self._geoms.append(_box(w, s, e, n))

    def add_point(self, lon, lat):
        try:
            x, y = float(lon), float(lat)
        except (TypeError, ValueError):
            self.warn(f"spatial: could not parse point {[lon, lat]}")
            return
        self._geoms.append({"type": "Point", "coordinates": [x, y]})

    def add_geometry(self, geom: dict | None):
        if not geom or not isinstance(geom, dict) or "type" not in geom:
            return
        gtype = geom["type"]
        if gtype == "Feature":
            return self.add_geometry(geom.get("geometry"))
        if gtype == "GeometryCollection":
            for g in geom.get("geometries", []):
                self.add_geometry(g)
            return
        if gtype in ("Polygon", "MultiPolygon", "Point", "MultiPoint", "LineString", "MultiLineString"):
            self._geoms.append({"type": gtype, "coordinates": geom.get("coordinates")})
        else:
            self.warn(f"spatial: unsupported geometry type {gtype}")

    # -- temporal ----------------------------------------------------------
    def set_temporal(self, start, end=None):
        if self._temporal:
            return
        s = normalize_datetime(start) if start else None
        e = normalize_datetime(end, end=True) if end else None
        if s or e:
            self._temporal = (s, e)
            return
        text = " / ".join(dict.fromkeys(str(v).strip() for v in (start, end) if v))
        if text and text not in ("..", ".. / .."):
            self.warn(f"temporal: could not parse {text!r}; kept as temporal_text")
            self.add_extra("temporal_text", text)

    # -- resources ---------------------------------------------------------
    def add_resource(self, url=None, name=None, *, format=None, description=None, mimetype=None, **extra):
        url = (url or "").strip()
        for r in self.resources:
            if url and r["url"] == url:
                return r
        res = {"url": url, "name": clean_text(name) or _name_from_url(url) or "Resource"}
        fmt = format or guess_format(url, mimetype) or guess_format(name)
        if fmt:
            res["format"] = fmt
        if mimetype:
            res["mimetype"] = mimetype
        desc = clean_text(description)
        if desc:
            res["description"] = desc
        for k, v in extra.items():
            if v not in (None, "", [], {}):
                res[k] = v if isinstance(v, (str, int, float, bool)) else _json(v)
        self.resources.append(res)
        return res

    # -- build -------------------------------------------------------------
    def build(self) -> dict:
        pkg: dict = {}
        if not self.title:
            self.warn("no title found in source record; using a placeholder")
        title = self.title or f"Untitled {self.source_format} record {self.external_id or ''}".strip()
        pkg["name"] = _package_name(title, self.external_id)
        pkg["title"] = title
        if self.notes:
            pkg["notes"] = self.notes
        else:
            self.warn("no description/abstract found")

        # people
        if self.creators:
            first = self.creators[0]
            self.set_field("author", first.get("name") or first.get("affiliation"))
            self.set_field("author_email", first.get("email"))
            if len(self.creators) > 1 or any(len(c) > 1 for c in self.creators):
                self.add_extra("creators", self.creators)
        if self.contacts:
            c = self.contacts[0]
            self.set_field("maintainer", c.get("name") or c.get("affiliation"))
            self.set_field("maintainer_email", c.get("email"))
            if len(self.contacts) > 1 or any(set(c) - {"name", "email"} for c in self.contacts):
                self.add_extra("contacts", self.contacts)
        self._ndp_people()

        # license
        lic = None
        for v in self._license_values:
            lic = map_license(v)
            if lic:
                break
        if lic:
            pkg["license_id"] = lic
        else:
            self.warn("no recognizable license; license_id left unset")
        if self._license_values:
            self.add_extra("rights_statement", "; ".join(self._license_values))

        for k in ("author", "author_email", "maintainer", "maintainer_email", "version"):
            if k in self.fields:
                pkg[k] = self.fields[k]

        # spatial / temporal
        geom = self._consolidated_geometry()
        if geom:
            self._extras["spatial"] = [_json(geom)]
        self._ndp_spatial(geom)
        if self._temporal:
            s, e = self._temporal
            self._set_extra("startDateTime", s)
            self._set_extra("endDateTime", e)
            if s and e:
                self._extras["temporal"] = [_json({"startTime": s, "endTime": e})]
            else:
                # NDP requires both ends in extras:temporal; keep the known end in start/endDateTime.
                self.warn("temporal: coverage is open-ended; extras.temporal omitted, see startDateTime/endDateTime")
        self._set_extra("spatialRes", self.spatial_resolution)
        self._ndp_dataset_fields()

        # provenance
        self._extras.pop("source_format", None)
        self.add_extra("source_format", self.source_format)
        if self.external_id:
            self.add_extra("source_identifier", self.external_id)
        if self.source_url:
            self.add_extra("source_url", self.source_url)

        # resources
        if not self.resources:
            fallback = self.source_url
            if not fallback and self.external_id:
                doi = extract_doi(self.external_id)
                fallback = f"https://doi.org/{doi}" if doi else (self.external_id if is_url(self.external_id) else None)
            self.warn("no distributions/resources in source; added landing page as resource"
                      if fallback else "no resources found; added empty placeholder resource")
            self.add_resource(fallback or "", "Landing page", format="HTML" if fallback else None,
                              mimetype="text/html" if fallback else None, description="Dataset landing page")
        self._ndp_resources()
        pkg["resources"] = self.resources

        if not self._tags:
            self.warn("no keywords in source; NDP requires tags for public datasets")
        pkg["tags"] = [{"name": n, "display_name": d} for n, d in self._tags.items()]
        pkg["extras"] = [{"key": k, "value": "; ".join(v) if len(v) > 1 else v[0]} for k, v in self._extras.items()]
        return pkg

    # -- NDP extras (see package_validation.py) ----------------------------
    def _set_extra(self, key: str, value):
        """Set an NDP extra, replacing any value a mapper may have added under the same key."""
        if value not in (None, ""):
            self._extras[key] = [value if isinstance(value, str) else _json(value)]

    def _ndp_people(self):
        names = [c["name"] for c in self.creators if c.get("name")] or \
            [c["affiliation"] for c in self.creators if c.get("affiliation")]
        self._set_extra("creatorName", "; ".join(dict.fromkeys(names)))
        self._set_extra("creatorEmail", next((c["email"] for c in self.creators
                                              if c.get("email") and _EMAIL_RE.match(c["email"])), None))
        self._set_extra("creatorWebsite", next((c["identifier"] for c in self.creators
                                                if is_url(c.get("identifier") or "")), None))
        affs = [c["affiliation"] for c in self.creators if c.get("affiliation") and c.get("name")]
        self._set_extra("creatorAffiliation", "; ".join(dict.fromkeys(affs)))

        poc = next((c for c in self.contacts if c.get("name") or c.get("affiliation")), None)
        if poc is None and self.creators:
            poc = self.creators[0]
            self.warn("no contact point in source; pocName/pocEmail taken from the first creator")
        if poc:
            self._set_extra("pocName", poc.get("name") or poc.get("affiliation"))
            email = poc.get("email") if _EMAIL_RE.match(poc.get("email") or "") else None
            self._set_extra("pocEmail", email)
            if not email:
                self.warn("no point-of-contact email in source; NDP requires extras.pocEmail for public datasets")
            if is_url(poc.get("website") or poc.get("identifier") or ""):
                self._set_extra("pocWebsite", poc.get("website") or poc.get("identifier"))

        self._extras.pop("publisher", None)
        self._set_extra("publisherName", self.publisher.get("name"))
        self._set_extra("publisherEmail", self.publisher.get("email"))
        self._set_extra("publisherWebsite", self.publisher.get("website"))

    def _ndp_spatial(self, geom: dict | None):
        if geom:
            self._set_extra("spatialCovFormat", "geojson")
            self._set_extra("spatialCov", _json(geom))
            env = _envelope(geom)
            if env:
                self._set_extra("dataBboxFormat", "geojson")
                self._set_extra("dataBbox", _json(env))
        elif self._extras.get("spatial_place"):
            self._set_extra("spatialCovFormat", "text")
            self._set_extra("spatialCov", "; ".join(self._extras["spatial_place"]))

    def _ndp_dataset_fields(self):
        upload = map_upload_type(self.resource_type)
        self._set_extra("uploadType", upload)
        self._set_extra("issueDate", self.issued)
        if self.modified:
            self._set_extra("lastUpdateDate", self.modified)
        elif self.issued:
            self._set_extra("lastUpdateDate", self.issued)
            self.warn("no modification date in source; lastUpdateDate set to issueDate")
        else:
            self.warn("no issue or modification date in source; NDP requires extras.lastUpdateDate")
        page = self.landing_page or (self.source_url if is_url(self.source_url or "") else None)
        self._set_extra("datasetPageUrl", page)
        self._set_extra("docsURL", self.docs_urls[0] if self.docs_urls else None)
        if len(self.docs_urls) > 1:
            self._set_extra("documentation_urls", "; ".join(self.docs_urls))

    def _ndp_resources(self):
        missing = 0
        for r in self.resources:
            r.setdefault("status", "active")
            mt = r.get("mimetype")
            if mt and ";" in mt:  # NDP mimetype must be a bare type/subtype; keep parameters separately
                r["media_type"] = mt
                r["mimetype"] = mt.split(";")[0].strip()
            if not r.get("mimetype"):
                guess = guess_mimetype(r.get("format")) or guess_mimetype(guess_format(r.get("url")))
                if guess:
                    r["mimetype"] = guess
                else:
                    missing += 1
            if not r.get("description"):
                fmt = f"{r['format']} " if r.get("format") else ""
                r["description"] = f"{fmt}resource '{r['name']}' from the source record"
        if missing:
            self.warn(f"{missing} resource(s) without a known mimetype; NDP requires it for public datasets")

    def _consolidated_geometry(self) -> dict | None:
        geoms = self._geoms
        if not geoms:
            return None
        uniq = []
        for g in geoms:
            if g not in uniq:
                uniq.append(g)
        if len(uniq) == 1:
            return uniq[0]
        types = {g["type"] for g in uniq}
        if types <= {"Polygon", "MultiPolygon"}:
            polys = []
            for g in uniq:
                polys.extend([g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"])
            self.warn(f"spatial: {len(uniq)} areas combined into one MultiPolygon")
            return {"type": "MultiPolygon", "coordinates": polys}
        if types <= {"Point", "MultiPoint"}:
            pts = []
            for g in uniq:
                pts.extend([g["coordinates"]] if g["type"] == "Point" else g["coordinates"])
            return {"type": "MultiPoint", "coordinates": pts}
        # Mixed geometry types: fall back to an envelope covering everything.
        xs, ys = [], []
        for g in uniq:
            for x, y in _iter_coords(g["coordinates"]):
                xs.append(x)
                ys.append(y)
        self.warn(f"spatial: mixed geometry types {sorted(types)} merged into one bounding box")
        return _box(min(xs), min(ys), max(xs), max(ys))


_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _envelope(geom: dict) -> dict | None:
    """Rectangular bounding-box polygon of a geometry (None for points/zero-area)."""
    xs, ys = [], []
    for x, y in _iter_coords(geom.get("coordinates")):
        xs.append(x)
        ys.append(y)
    if not xs or min(xs) == max(xs) or min(ys) == max(ys):
        return None
    return _box(min(xs), min(ys), max(xs), max(ys))


def _box(w, s, e, n) -> dict:
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def _iter_coords(c):
    if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
        yield float(c[0]), float(c[1])
    elif isinstance(c, (list, tuple)):
        for sub in c:
            yield from _iter_coords(sub)


def _name_from_url(url: str) -> str | None:
    if not url:
        return None
    path = re.split(r"[?#]", url)[0].rstrip("/")
    if path.endswith("/content"):
        path = path[: -len("/content")]
    tail = path.rsplit("/", 1)[-1]
    return tail or None


def _package_name(title: str, external_id: str | None) -> str:
    """Stable, unique CKAN name: slug(title) + short hash of the external id."""
    key = external_id or title
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    base = slugify(title, 100 - 9) or "dataset"
    return f"{base}-{digest}"


def validate_package(pkg: dict) -> list[str]:
    """Structural checks against CKAN's default package schema. Returns error strings."""
    errors = []
    name = pkg.get("name", "")
    if not re.fullmatch(r"[a-z0-9_-]{2,100}", name or ""):
        errors.append(f"invalid name {name!r}")
    if not pkg.get("title"):
        errors.append("missing title")
    for e in pkg.get("extras", []):
        if set(e) != {"key", "value"}:
            errors.append(f"extra has unexpected keys: {sorted(e)}")
        if not isinstance(e.get("value"), str):
            errors.append(f"extra {e.get('key')!r} value is not a string")
        if e.get("key") in RESERVED_KEYS or e.get("key") == "license":
            errors.append(f"extra uses reserved key {e.get('key')!r}")
    keys = [e.get("key") for e in pkg.get("extras", [])]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        errors.append(f"duplicate extras keys: {sorted(dupes)}")
    for t in pkg.get("tags", []):
        if not re.fullmatch(r"[\w\-. ]{2,100}", t.get("name", ""), flags=re.UNICODE):
            errors.append(f"invalid tag name {t.get('name')!r}")
    if not pkg.get("resources"):
        errors.append("no resources")
    for r in pkg.get("resources", []):
        if "url" not in r:
            errors.append(f"resource without url key: {r.get('name')}")
    if pkg.get("license_id") and pkg["license_id"] not in CKAN_LICENSES:
        errors.append(f"unknown license_id {pkg['license_id']!r}")
    for e in pkg.get("extras", []):
        if e.get("key") in ("spatial", "temporal"):
            try:
                json.loads(e["value"])
            except ValueError:
                errors.append(f"extra {e['key']!r} is not valid JSON")
    return errors
