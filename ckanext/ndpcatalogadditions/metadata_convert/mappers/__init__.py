"""One mapper per source schema. Each is `convert(parsed: Parsed, b: CkanBuilder) -> None`."""

from . import datacite, dcat, dublincore, iso19115, schemaorg, stac

MAPPERS = {
    "datacite": datacite.convert,
    "dublincore": dublincore.convert,
    "schemaorg": schemaorg.convert,
    "dcat": dcat.convert,
    "iso19115": iso19115.convert,
    "stac": stac.convert,
}
