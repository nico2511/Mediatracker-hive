"""Free-Telecharger HTML to neutral page objects. No network."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from mediatracker.core.hosters import PAGE_TEXT_TO_HOSTER

_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"})
_IGNORED_QUALITIES = frozenset({"cam", "ts", "r5", "dvdscr", "hdcam", "hdts", "telesync", "telecine"})
_YEAR = re.compile(r"Année\s*:\s*(\d{4})", re.I)
_QUALITY_LINE = re.compile(r"Qualité\s*:\s*([^\n]+)", re.I)
_LANGUAGE_LINE = re.compile(r"Langue\s*:\s*([^\n]+)", re.I)
_EPISODE = re.compile(r"[EeÉé]pisode\s*(\d+)", re.I)
_SEASON_TITLE = re.compile(r"(?i)(?:saison|season)\s*(\d+)")
_SEASON_URL = re.compile(r"(?i)saison-?(\d+)")
_LANGUAGE_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿ]+")
_MULTI_MARKER = "MULTI"
_LANGUAGE_FIELD_TOKENS = {
    "vf": ("VF", "VF"),
    "vff": ("VFF", "VFF"),
    "vfi": ("VFI", "VFI"),
    "vf2": ("VF2", "VF2"),
    "vof": ("VOF", "VOF"),
    "french": ("VFF", "FRENCH"),
    "francais": ("VFF", "FRENCH"),
    "français": ("VFF", "FRENCH"),
    "truefrench": ("VFF", "TRUEFRENCH"),
    "vostfr": ("VOSTFR", "VOSTFR"),
    "vost": ("VOST", "VOST"),
    "subfrench": ("VFF", "SUBFRENCH"),
    "vfq": ("VFQ", "VFQ"),
    "quebecois": ("VFQ", "VFQ"),
    "québécois": ("VFQ", "VFQ"),
    "vo": ("VO", "VO"),
    "multi": ("MULTI", "MULTI"),
    "multilangues": ("MULTI", "MULTI"),
}
@dataclass(frozen=True)
class Heading:
    title: str
    year: int | None
    season: int | None
    quality: str | None
    language: str | None
    media_type: str | None


@dataclass(frozen=True)
class SearchHit:
    title: str
    year: int | None
    media_type: str | None
    season: int | None
    quality: str | None
    language: str | None
    url: str
    category: str | None = None
    rejection: str | None = None


@dataclass(frozen=True)
class PageLink:
    url: str
    hoster: str
    season: int | None
    episode: int | None
    quality: str | None
    language: str | None
    filename: str | None = None
    intermediate: bool = False


_MOVIE_CATEGORIES = (
    "films bluray 4k",
    "films hd",
    "films hdlight",
    "films sd",
    "films web-dl",
    "films bluray",
    "films dvdrip",
    "films webrip",
    "films hdrip",
)
_SERIES_CATEGORIES = (
    "saisons en cours vf",
    "saison terminee vf",
    "saisons en cours vostfr",
    "saison terminee vostfr",
    "series tv",
    "séries tv",
    "saisons en cours vf hd",
    "saison terminee vf hd",
)


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {})
        self._stack: list[_Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag, attrs, push=tag not in _VOID)

    def _open(self, tag: str, attrs: list[tuple[str, str | None]], *, push: bool = True) -> None:
        node = _Node(tag, {key: value or "" for key, value in attrs})
        self._stack[-1].children.append(node)
        if push and tag not in _VOID:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        while len(self._stack) > 1:
            node = self._stack.pop()
            if node.tag == tag:
                break

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._stack[-1].children.append(data)


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[_Node | str] = []

    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def text(self) -> str:
        parts: list[str] = []

        def walk(node: _Node | str) -> None:
            if isinstance(node, str):
                parts.append(node)
                return
            for child in node.children:
                walk(child)

        walk(self)
        return " ".join(" ".join(parts).split())

    def find_all(
        self,
        tag: str | None = None,
        class_name: str | None = None,
        element_id: str | None = None,
    ) -> list[_Node]:
        found: list[_Node] = []

        def walk(node: _Node) -> None:
            for child in node.children:
                if not isinstance(child, _Node):
                    continue
                tag_ok = tag is None or child.tag == tag
                class_ok = class_name is None or class_name in child.classes()
                id_ok = element_id is None or child.attrs.get("id") == element_id
                if tag_ok and class_ok and id_ok:
                    found.append(child)
                walk(child)

        walk(self)
        return found

    def css_first(self, selector: str) -> _Node | None:
        tag, class_name, element_id = _parse_selector(selector)
        for node in self.find_all(tag, class_name, element_id):
            return node
        return None


def _parse_selector(selector: str) -> tuple[str | None, str | None, str | None]:
    if selector.startswith("div."):
        return "div", selector.split(".", 1)[1], None
    if selector.startswith("div#"):
        return "div", None, selector.split("#", 1)[1]
    if selector == "table.gridtable":
        return "table", "gridtable", None
    if selector == 'input[name="lien"]':
        return "input", None, None
    return None, None, None


def _tree(html: str) -> _Node:
    parser = _Tree()
    parser.feed(html)
    return parser.root


def _content_type(category: str | None) -> str | None:
    if not category:
        return None
    low = category.lower().strip()
    for item in _MOVIE_CATEGORIES:
        if item in low:
            return "film"
    for item in _SERIES_CATEGORIES:
        if item in low:
            return "series"
    return None


def _clean_title(title: str) -> str:
    if not title:
        return ""
    cleaned = re.sub(r"\s*-\s*saison\s*\d+.*$", "", title, flags=re.I)
    cleaned = re.sub(
        r"\s*-\s*(?:HD|SD|4K|1080p|720p|VFF|VFQ|VF|MULTI|VOSTFR|VO|WEB|Bluray).*$",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"\s*\[.*?\].*$", "", cleaned)
    cleaned = re.sub(r"\s*\(.*?\)\s*$", "", cleaned)
    return cleaned.strip()


def _tags_from_title(title: str) -> tuple[str | None, str | None]:
    upper = (title or "").upper()
    quality = None
    if "4K" in upper or "2160" in upper:
        quality = "4K"
    elif "1080" in upper:
        quality = "1080p"
    elif "HDLIGHT" in upper:
        quality = "HDLight"
    elif "HD" in upper or "720" in upper:
        quality = "HD"
    elif "SD" in upper:
        quality = "SD"
    language = None
    for token in ("VOSTFR", "VFF", "VFQ", "MULTI", "VF", "VO"):
        if token in upper:
            language = token if token != "VF" else "VF"
            break
    return quality, language


def _extract_quality(text: str) -> str:
    if not text:
        return "Unknown"
    upper = text.upper()
    if "2160" in upper or "4K" in upper or "UHD" in upper:
        resolution = "4K"
    elif "1080" in upper:
        resolution = "1080p"
    elif "720" in upper:
        resolution = "HD"
    elif "480" in upper:
        resolution = "SD"
    else:
        resolution = ""
    if "REMUX" in upper:
        release_type = "REMUX"
    elif "BLURAY" in upper or "BLU-RAY" in upper or "BDRIP" in upper:
        release_type = "BLURAY"
    elif "WEB-DL" in upper or "WEBDL" in upper:
        release_type = "WEB-DL"
    elif "HDLIGHT" in upper or "LIGHT" in upper:
        release_type = "HDLight"
    elif "WEBRIP" in upper:
        release_type = "WEBRIP"
    elif "HDRIP" in upper:
        release_type = "HDRIP"
    elif "HDTV" in upper:
        release_type = "HDTV"
    elif "DVDRIP" in upper:
        release_type = "DVDRIP"
    else:
        release_type = ""
    if resolution and release_type:
        return f"{release_type} {resolution}".replace("BLURAY HD", "HD").replace("WEB-DL HD", "HD")
    return resolution or release_type or "Unknown"


def _ignored_quality(quality: str) -> bool:
    low = (quality or "").lower()
    return any(item in low for item in _IGNORED_QUALITIES)


def _parse_language_field(text: str) -> tuple[list[str], bool]:
    if not text:
        return [], False
    is_multi = _MULTI_MARKER in text.upper()
    tags: list[str] = []
    for token in _LANGUAGE_TOKEN_SPLIT.split(text):
        entry = _LANGUAGE_FIELD_TOKENS.get(token.lower())
        if entry is None:
            continue
        tag = entry[0]
        if tag not in tags:
            tags.append(tag)
    return tags, is_multi


def _canonical_language(text: str) -> str:
    tags, is_multi = _parse_language_field(text)
    if not tags:
        return "MULTI" if is_multi else "Unknown"
    if is_multi or len(tags) > 1:
        return "MULTI"
    return tags[0]


def _normalize_hoster(text: str) -> str:
    lowered = (text or "").lower()
    for needle, name in PAGE_TEXT_TO_HOSTER:
        if needle in lowered:
            return name
    return lowered.split()[0] if lowered.split() else "unknown"


def _abs_url(base: str, href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return urljoin(base.rstrip("/") + "/", href.lstrip("/"))


_SLUG_INTERMEDIATE = re.compile(r"^[a-z0-9]{3,12}-episode_\d+\b", re.I)


def _normalize_lien_url(base: str, href: str) -> str:
    """Resolve catalogue-relative slugs onto the ``liens.`` host when needed."""
    text = (href or "").strip()
    if not text:
        return ""
    absolute = _abs_url(base, text)
    host = (urlparse(absolute).hostname or "").lower()
    if host.startswith("liens."):
        return absolute
    slug = text.lstrip("/")
    if _SLUG_INTERMEDIATE.match(slug):
        catalog = (urlparse(base).hostname or "").lower().removeprefix("www.")
        if catalog:
            scheme = urlparse(base).scheme or "https"
            return f"{scheme}://liens.{catalog}/{slug}"
    return absolute


def _episode_blocks(link_root: _Node) -> list[_Node]:
    """Episode rows use ``div#main`` on the live site; older mocks used ``div.main``."""
    blocks = link_root.find_all("div", "main")
    if blocks:
        return blocks
    return link_root.find_all("div", None, element_id="main")


def _season_from(title: str, url: str) -> int | None:
    match = _SEASON_TITLE.search(title or "")
    if match:
        return int(match.group(1))
    match = _SEASON_URL.search(url or "")
    if match:
        return int(match.group(1))
    return None


def parse_search(html: str, base_url: str, *, want_type: str | None = None) -> list[SearchHit]:
    root = _tree(html)
    hits: list[SearchHit] = []
    for container in root.find_all("div", "container"):
        title_node = container.css_first("div.titre1")
        if title_node is None:
            continue
        link_node = None
        for child in title_node.children:
            if isinstance(child, _Node) and child.tag == "a":
                link_node = child
                break
        raw_title = title_node.text().strip()
        href = link_node.attrs.get("href", "") if link_node is not None else ""
        if not raw_title:
            continue
        category = ""
        for bold in container.find_all("b", None):
            category = category or bold.text().strip()
        detected = _content_type(category)
        if want_type and detected and detected != want_type:
            continue
        year_match = _YEAR.search(container.text())
        year = int(year_match.group(1)) if year_match else None
        title = _clean_title(raw_title)
        quality, language = _tags_from_title(raw_title)
        quality = quality or _extract_quality(raw_title)
        language = language or _canonical_language(raw_title)
        season = _season_from(raw_title, href)
        url = _abs_url(base_url, href)
        rejection = None
        if _ignored_quality(quality):
            rejection = "quality"
        hits.append(
            SearchHit(
                title=title,
                year=year,
                media_type=detected,
                season=season,
                quality=quality,
                language=language,
                url=url,
                category=category,
                rejection=rejection,
            )
        )
    return hits


def parse_links(html: str, page_url: str, *, base_url: str | None = None) -> tuple[Heading, list[PageLink]]:
    root = _tree(html)
    base = base_url or f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
    page_text = root.text()
    quality_match = _QUALITY_LINE.search(page_text)
    quality = _extract_quality(quality_match.group(1) if quality_match else "")
    language_match = _LANGUAGE_LINE.search(page_text)
    language = _canonical_language(language_match.group(1) if language_match else "")
    title_node = root.css_first("div.titre1")
    title = title_node.text().strip() if title_node is not None else ""
    year_match = _YEAR.search(page_text)
    year = int(year_match.group(1)) if year_match else None
    season = _season_from(title, page_url)
    media_type = "series" if season is not None or "series-" in page_url.lower() else "film"
    heading = Heading(
        title=_clean_title(title),
        year=year,
        season=season,
        quality=quality if not _ignored_quality(quality) else None,
        language=language,
        media_type=media_type,
    )
    links: list[PageLink] = []
    link_root = root.css_first("div#link")
    if link_root is None:
        return heading, links
    for block in _episode_blocks(link_root):
        episode = None
        hoster_text = ""
        for child in block.children:
            if isinstance(child, _Node) and child.tag == "p":
                hoster_text = child.text().strip()
                episode_match = _EPISODE.search(hoster_text)
                if episode_match:
                    episode = int(episode_match.group(1))
                    hoster_text = _EPISODE.sub("", hoster_text).strip()
                break
        hoster = _normalize_hoster(hoster_text)
        url = ""
        intermediate = False
        for node in block.find_all("input", None):
            if node.attrs.get("name") == "lien":
                url = (node.attrs.get("value") or "").strip()
                break
        if not url:
            for node in block.find_all("a", None):
                url = (node.attrs.get("href") or "").strip()
                if url:
                    break
        if not url:
            continue
        abs_url = _normalize_lien_url(base, url)
        host = (urlparse(abs_url).hostname or "").lower()
        intermediate = host.startswith("liens.")
        links.append(
            PageLink(
                url=abs_url,
                hoster=hoster,
                season=season,
                episode=episode,
                quality=heading.quality,
                language=heading.language,
                intermediate=intermediate,
            )
        )
    return heading, links


def parse_intermediate(html: str, base_url: str) -> list[tuple[str, str]]:
    root = _tree(html)
    table = None
    for node in root.find_all("table", "gridtable"):
        table = node
        break
    if table is None:
        return []
    rows: list[tuple[str, str]] = []
    for row in table.find_all("tr", None):
        cells = [child for child in row.children if isinstance(child, _Node) and child.tag == "td"]
        if len(cells) < 3:
            continue
        hoster_text = cells[1].text()
        hoster_match = re.search(r"\[([^\]]+)\]", hoster_text)
        hoster = _normalize_hoster(hoster_match.group(1) if hoster_match else hoster_text)
        link_node = None
        for child in cells[2].children:
            if isinstance(child, _Node) and child.tag == "a":
                link_node = child
                break
        if link_node is None:
            continue
        href = link_node.attrs.get("href", "")
        if not href:
            continue
        rows.append((_abs_url(base_url, href), hoster))
    return rows


def map_media_quality(quality: str | None) -> str | None:
    if not quality:
        return None
    upper = quality.upper()
    if "4K" in upper or "2160" in upper:
        return "4K"
    if "1080" in upper:
        return "1080p"
    if "HDLight" in quality:
        return "HDLight"
    if "HD" in upper or "720" in upper or "WEB" in upper or "BLURAY" in upper:
        return "HD"
    if "SD" in upper or "480" in upper:
        return "SD"
    return quality
