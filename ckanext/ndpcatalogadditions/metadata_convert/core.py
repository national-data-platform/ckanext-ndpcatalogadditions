"""Public entry point: metadata_convert(record, fmt="auto") -> ConversionResult."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .ckan import CkanBuilder, validate_package
from .detect import FORMATS, FormatError, Parsed, detect, load
from .mappers import MAPPERS


# Which parsed input kinds each mapper can read.
_ACCEPTS = {
    "datacite": {"json", "xml"},
    "dublincore": {"json", "xml"},
    "schemaorg": {"json"},
    "dcat": {"json", "rdf"},
    "iso19115": {"xml"},
    "stac": {"json"},
}


@dataclass
class ConversionResult:
    package: dict
    source_format: str
    external_id: str | None
    warnings: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    original: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self, include_original: bool = False) -> dict:
        d = asdict(self)
        if not include_original:
            d.pop("original")
        return d


def metadata_convert(record, fmt: str = "auto") -> ConversionResult:
    """Convert one metadata record to a CKAN package dict.

    record: str | bytes | dict | pathlib.Path holding one record in DataCite (JSON/XML),
            Dublin Core (XML/JSON), schema.org Dataset (JSON-LD), DCAT/DCAT-AP (RDF or
            DCAT-US JSON), ISO 19115/19139 (XML) or STAC (Collection/Item/Catalog JSON).
    fmt:    one of FORMATS, or "auto" to detect.
    """
    parsed: Parsed = load(record)
    detected = detect(parsed)
    if fmt in (None, "", "auto"):
        fmt = detected
    elif fmt not in FORMATS:
        raise FormatError(f"unknown format {fmt!r}; expected one of {', '.join(FORMATS)} or 'auto'")
    if parsed.kind == "xml" and fmt == "dcat":
        parsed.kind, parsed.data, parsed.rdf_format = "rdf", parsed.text, "xml"
    if parsed.kind not in _ACCEPTS[fmt]:
        raise FormatError(f"{fmt} input must be {' or '.join(sorted(_ACCEPTS[fmt])).upper()}, got {parsed.kind.upper()}")

    builder = CkanBuilder(fmt)
    if fmt != detected:
        builder.warn(f"format forced to {fmt!r} but input looks like {detected!r}")
    MAPPERS[fmt](parsed, builder)
    package = builder.build()
    return ConversionResult(
        package=package,
        source_format=fmt,
        external_id=builder.external_id,
        warnings=builder.warnings,
        unmapped=builder.unmapped,
        errors=validate_package(package),
        original=parsed.text,
    )
