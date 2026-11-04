"""Convert DataCite, Dublin Core, schema.org, DCAT, ISO 19115 and STAC metadata to CKAN JSON."""

from .core import ConversionResult, metadata_convert
from .detect import FORMATS, FormatError

__all__ = ["metadata_convert", "ConversionResult", "FORMATS", "FormatError"]
