"""Small helpers shared by the mappers: text cleanup, dates, identifiers, tracking."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table"}


class _HtmlToText(HTMLParser):
    """Convert simple HTML to Markdown-ish plain text (CKAN notes are Markdown)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._href: list[str | None] = []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.out.append("\n" if tag == "br" else "\n\n")
        if tag == "li":
            self.out.append("- ")
        if tag == "a":
            self._href.append(dict(attrs).get("href"))
            self.out.append("[")
        if tag in ("em", "i"):
            self.out.append("*")
        if tag in ("strong", "b"):
            self.out.append("**")

    def handle_endtag(self, tag):
        if tag == "a":
            href = self._href.pop() if self._href else None
            self.out.append(f"]({href})" if href else "]")
        if tag in ("em", "i"):
            self.out.append("*")
        if tag in ("strong", "b"):
            self.out.append("**")
        if tag in _BLOCK_TAGS and tag != "br":
            self.out.append("\n\n")

    def handle_data(self, data):
        self.out.append(data)


_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def clean_text(value) -> str | None:
    """Normalize a free-text field: unescape HTML, strip tags, tidy whitespace."""
    if value is None:
        return None
    text = str(value)
    # Doubly-escaped HTML (e.g. Zenodo oai_dc): "&lt;p&gt;" with no literal tags.
    if "<" not in text and re.search(r"&lt;/?[a-zA-Z]", text):
        text = html.unescape(text)
    if _TAG_RE.search(text):
        parser = _HtmlToText()
        parser.feed(text)
        parser.close()
        text = "".join(parser.out)
    else:
        text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("\r\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None


def first(values):
    """First non-empty item of an iterable (or the value itself if scalar)."""
    if values is None:
        return None
    if isinstance(values, (list, tuple)):
        for v in values:
            if v not in (None, "", [], {}):
                return v
        return None
    return values


def as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

_DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s\"<>]+)", re.IGNORECASE)


def extract_doi(value) -> str | None:
    """Return a bare, lowercase DOI from 'doi:..', 'https://doi.org/..' or '10.x/..' strings."""
    if not value:
        return None
    s = str(value).strip()
    if not (s.lower().startswith(("doi:", "10.")) or "doi.org/" in s.lower()):
        return None
    m = _DOI_RE.search(s)
    return m.group(1).rstrip(".").lower() if m else None


def is_url(value) -> bool:
    return isinstance(value, str) and re.match(r"^(https?|ftp|s3)://", value.strip(), re.I) is not None


def pick_identifier(candidates) -> str | None:
    """Choose the most stable identifier: DOI > http(s) URL > anything else."""
    cands = [str(c).strip() for c in candidates if c not in (None, "")]
    for c in cands:
        doi = extract_doi(c)
        if doi:
            return f"doi:{doi}"
    for c in cands:
        if is_url(c):
            return c
    for c in cands:
        if not c.lower().startswith("oai:"):
            return c
    return cands[0] if cands else None


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_OPEN_ENDS = {"", "..", "now", "present", "ongoing", "unknown", "none", "null"}


def normalize_datetime(value, end: bool = False) -> str | None:
    """Return 'YYYY-MM-DDTHH:MM:SSZ' for common date shapes, None if unparseable/open.

    Partial dates are expanded to the start (or end, when end=True) of the period.
    """
    if value is None:
        return None
    s = str(value).strip()
    if s.lower() in _OPEN_ENDS:
        return None
    m = re.fullmatch(r"(\d{4})", s)
    if m:
        return f"{s}-12-31T23:59:59Z" if end else f"{s}-01-01T00:00:00Z"
    m = re.fullmatch(r"(\d{4})-(\d{2})", s)
    if m:
        if end:
            last = _days_in_month(int(m.group(1)), int(m.group(2)))
            return f"{s}-{last:02d}T23:59:59Z"
        return f"{s}-01T00:00:00Z"
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return f"{s}T23:59:59Z" if end else f"{s}T00:00:00Z"
    iso = s.replace("Z", "+00:00").replace(" ", "T", 1)
    iso = re.sub(r"(\.\d{6})\d+", r"\1", iso)  # trim >6 fractional digits
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _days_in_month(year: int, month: int) -> int:
    import calendar

    return calendar.monthrange(year, month)[1]


def split_interval(value) -> tuple[str | None, str | None] | None:
    """Split 'start/end' (ISO 8601 interval) into its parts; None if not an interval."""
    if not isinstance(value, str) or "/" not in value:
        return None
    parts = value.split("/")
    if len(parts) != 2:
        return None
    return parts[0].strip(), parts[1].strip()


# ---------------------------------------------------------------------------
# Unmapped-field tracking
# ---------------------------------------------------------------------------


class Tracker:
    """Records which source paths a mapper consumed, to report the rest as unmapped."""

    def __init__(self, ignore: set[str] | None = None, ignore_leaf_names: set[str] | None = None):
        self.used: set[str] = set()
        self.ignore: set[str] = set(ignore or ())
        self.ignore_leaf_names: set[str] = set(ignore_leaf_names or ())

    def use(self, *paths: str):
        self.used.update(p for p in paths if p)

    def _covered(self, path: str, prefixes: set[str]) -> bool:
        for p in prefixes:
            if path == p or path.startswith(p + ".") or path.startswith(p + "/") or path.startswith(p + "["):
                return True
        return False

    def leftovers(self, items) -> dict[str, list[str]]:
        """Group (path, value) leaves that no mapper read into {extras key: [values]}.

        The key is the dotted source path with list markers removed, e.g. 'fundingReferences.awardTitle'.
        """
        out: dict[str, list[str]] = {}
        for path, value in items:
            if self._covered(path, self.used) or self._covered(path, self.ignore):
                continue
            if re.split(r"[./]", path.replace("[]", ""))[-1] in self.ignore_leaf_names:
                continue
            text = scalar_text(value)
            if text:
                values = out.setdefault(path.replace("[]", ""), [])
                if text not in values:
                    values.append(text)
        return out

    def unmapped(self, all_paths, depth: int | None = None, sep: str = ".") -> list[str]:
        out = set()
        for path in all_paths:
            if self._covered(path, self.used) or self._covered(path, self.ignore):
                continue
            if re.split(r"[./]", path.replace("[]", ""))[-1] in self.ignore_leaf_names:
                continue
            if depth:
                path = sep.join(path.split(sep)[:depth])
            out.add(path)
        return sorted(out)


def scalar_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return text or None


def json_leaf_items(data, prefix: str = "") -> list[tuple[str, object]]:
    """(path, value) for every non-empty leaf of a JSON value, list indices collapsed to '[]'."""
    items: list[tuple[str, object]] = []
    if isinstance(data, dict):
        for k, v in data.items():
            items.extend(json_leaf_items(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(data, list):
        for v in data:
            items.extend(json_leaf_items(v, f"{prefix}[]"))
    elif data not in (None, ""):
        items.append((prefix, data))
    return items


def json_leaf_paths(data, prefix: str = "") -> list[str]:
    """Leaf paths of a JSON value, with list indices collapsed to '[]'. Empty values skipped."""
    paths: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            paths.extend(json_leaf_paths(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(data, list):
        for v in data:
            paths.extend(json_leaf_paths(v, f"{prefix}[]"))
    elif data not in (None, ""):
        paths.append(prefix)
    return sorted(set(paths))


class TJ:
    """Tracked JSON: a read-only view over dict/list data that marks leaf paths as used.

    Reading a scalar marks its path; reading a container does not (its leaves are
    marked as they are read). `.raw` returns the plain data and marks the whole subtree.
    """

    __slots__ = ("_d", "_p", "_t")

    def __init__(self, data, tracker: Tracker, path: str = ""):
        self._d, self._t, self._p = data, tracker, path

    def _child_path(self, key) -> str:
        return f"{self._p}.{key}" if self._p else str(key)

    def _wrap(self, value, path):
        if isinstance(value, (dict, list)):
            return TJ(value, self._t, path)
        self._t.use(path)
        return value

    def get(self, key, default=None):
        if not isinstance(self._d, dict):
            return default
        path = self._child_path(key)
        if key not in self._d or self._d[key] is None:
            return default
        return self._wrap(self._d[key], path)

    def __getitem__(self, key):
        if isinstance(self._d, list):
            return self._wrap(self._d[key], f"{self._p}[]")
        return self._wrap(self._d[key], self._child_path(key))

    def __contains__(self, key):
        return isinstance(self._d, dict) and key in self._d

    def __iter__(self):
        if isinstance(self._d, list):
            for v in self._d:
                yield self._wrap(v, f"{self._p}[]")
        else:
            yield from self._d

    def items(self):
        for k in self._d:
            yield k, self.get(k)

    def keys(self):
        return self._d.keys()

    def __len__(self):
        return len(self._d)

    def __bool__(self):
        return bool(self._d)

    def is_list(self) -> bool:
        return isinstance(self._d, list)

    def is_dict(self) -> bool:
        return isinstance(self._d, dict)

    @property
    def raw(self):
        self._t.use(self._p)
        return self._d

    def __repr__(self):
        return f"TJ({self._p!r}, {self._d!r:.80})"


def unwrap(value):
    """Plain value for a possibly-tracked value (marks the subtree used)."""
    return value.raw if isinstance(value, TJ) else value


def tj_list(value) -> list:
    """Iterate a tracked value that may be a scalar, dict or list, as a list."""
    if value is None:
        return []
    if isinstance(value, TJ) and value.is_list():
        return list(value)
    return [value]
