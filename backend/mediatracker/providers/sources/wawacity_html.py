"""Wawacity HTML → titles, seasons, and dl-protect links.

Written from the v1 page shape (search blocks, episode rows, hoster cells).
It does not fetch anything and it does not touch dl-protect.
"""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from mediatracker.core.release_title import split_release_title
from mediatracker.library.parse import episode_bits_from_text

_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"})
_PREFIX = re.compile(r"(?i)^(films|séries|series|serie|animes|jeux)\s*[»>:|\-]\s*")
_YEAR = re.compile(r"\((19\d{2}|20\d{2})\)")
_YEAR_LINK = re.compile(r"[?&]year=(19\d{2}|20\d{2})", re.I)
_BLOCK_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
_TAG = re.compile(r"\[([^\]]+)\]")
_SEASON_TEXT = re.compile(r"(?i)(?:saison|season)\s*(\d+)")
_SEASON_URL = re.compile(r"(?i)(?:saison|season)[-_ ]?(\d+)|(?:^|[-_/])s(\d{1,2})(?:e\d{1,2})?(?:$|[-_.])")
_EPISODE = re.compile(
    r"(?i)(?:saison|season)\s*(\d+)\D{0,24}?(?:episode|épisode|ep\.?)\s*(\d+)"
    r"|s(\d{1,2})\s*e(\d{1,2})"
    r"|(?:episode|épisode|ep\.?)\s*(\d+)"
)
_QUALITY = (
    ("hdlight", "HDLight"),
    ("2160p", "4K"),
    ("uhd", "4K"),
    ("4k", "4K"),
    ("1080p", "1080p"),
    ("720p", "HD"),
    ("480p", "SD"),
    ("webrip", "HD"),
    ("webrrip", "HD"),
    ("web-dl", "HD"),
    ("webdl", "HD"),
    ("hd", "HD"),
    ("sd", "SD"),
)
_CAM = re.compile(r"(?i)\b(?:cam|hdcam|hdts|hdtc|telesync|dvdscr|dvd-scr|r5)\b|(?:^|[^a-z])ts(?:[^a-z]|$)")
_LANGUAGE = (
    ("vostfr", "VOSTFR"),
    ("multi", "MULTI"),
    ("truefrench", "VFF"),
    ("vff", "VFF"),
    ("vfq", "VFQ"),
    ("french", "VF"),
    ("vf", "VF"),
    ("vo", "VO"),
)
# Longer aliases first. Uptobox is recognised so the caller can drop it.
_HOSTERS = (
    ("dailyuploads", "dailyuploads"),
    ("daily-uploads", "dailyuploads"),
    ("daily uploads", "dailyuploads"),
    ("nitroflare", "nitroflare"),
    ("rapidgator", "rapidgator"),
    ("turbobit", "turbobit"),
    ("trbt.cc", "turbobit"),
    ("rg.to", "rapidgator"),
    ("1fichier", "1fichier"),
    ("uptobox", "uptobox"),
    ("uploady", "uploady"),
    ("tezfiles", "tezfiles"),
    ("katfile", "katfile"),
)
_PROTECT = ("dl-protect", "dlprotect", "protect-link")


@dataclass(frozen=True)
class Heading:
    title: str
    year: int | None
    season: int | None
    quality: str | None
    language: str | None
    media_type: str | None
    profile_hint: str = ""


@dataclass(frozen=True)
class SearchHit:
    title: str
    year: int | None
    media_type: str | None
    season: int | None
    quality: str | None
    language: str | None
    url: str
    episode: int | None = None
    rejection: str | None = None
    poster_url: str | None = None
    profile_hint: str = ""


@dataclass(frozen=True)
class PageLink:
    url: str
    hoster: str
    season: int | None
    episode: int | None
    quality: str | None
    language: str | None
    filename: str | None = None


@dataclass(frozen=True)
class AlternateQuality:
    title: str
    year: int | None
    quality: str | None
    language: str | None
    url: str
    rejection: str | None = None
    label: str = ""


class Node:
    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[Node | str] = []

    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def find_all(self, tag: str | None = None, class_name: str | None = None) -> list["Node"]:
        found: list[Node] = []

        def walk(node: Node) -> None:
            for child in node.children:
                if not isinstance(child, Node):
                    continue
                tag_ok = tag is None or child.tag == tag
                class_ok = class_name is None or class_name in child.classes()
                if tag_ok and class_ok:
                    found.append(child)
                walk(child)

        walk(self)
        return found

    def find(self, tag: str | None = None, class_name: str | None = None) -> "Node | None":
        found = self.find_all(tag, class_name)
        return found[0] if found else None

    def text(self) -> str:
        parts: list[str] = []

        def walk(node: Node) -> None:
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                else:
                    walk(child)

        walk(self)
        return re.sub(r"\s+", " ", "".join(parts)).strip()


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document", {})
        self._stack: list[Node] = [self.root]
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if self._skip:
            if name not in _VOID:
                self._skip += 1
            return
        if name in {"script", "style"}:
            self._skip = 1
            return
        node = Node(name, {key.lower(): value or "" for key, value in attrs})
        self._stack[-1].children.append(node)
        if name not in _VOID:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if self._skip:
            if name in {"script", "style"} or self._skip > 1:
                self._skip -= 1
            return
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == name:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self._skip or not data:
            return
        self._stack[-1].children.append(data)


def parse_html(raw: str) -> Node:
    parser = _Tree()
    parser.feed(raw)
    parser.close()
    return parser.root


def _meta_tokens(raw: str) -> str:
    """Bracket tags and language/quality suffix — not the display title."""
    text = (raw or "").strip()
    tags = " ".join(_TAG.findall(text))
    tail = text.rsplit("]", 1)[-1] if "]" in text else text
    parts = re.split(r"\s*[-–—]\s*", tail.strip())
    suffix = parts[-1] if len(parts) > 1 else ""
    return f"{tags} {suffix}".strip()


def _scoped_has(text: str, alias: str) -> bool:
    from mediatracker.core.quality_profile import _has

    folded = (text or "").lower()
    if alias.isdigit() or alias.endswith("p"):
        return alias.replace("p", "") in re.sub(r"\s+", "", folded)
    return _has(folded, alias)


def _quality_from_scan(scan: str) -> str | None:
    blob = (scan or "").strip()
    if not blob:
        return None
    if _scoped_has(blob, "hdlight"):
        if "1080" in re.sub(r"\s+", "", blob.lower()):
            return "HDLight1080"
        return "HDLight"
    for alias, name in _QUALITY:
        if alias == "hdlight":
            continue
        if _scoped_has(blob, alias):
            return name
    return None


def canon_quality(token: str) -> str | None:
    meta = _meta_tokens(token)
    tag_blob = " ".join(_TAG.findall(token or ""))
    return _quality_from_scan(f"{tag_blob} {meta}".strip())


def quality_from_meta_value(value: str) -> str | None:
    """Bold « Qualité » list value (e.g. ``1080p`` with no surrounding title)."""
    return _quality_from_scan((value or "").strip())


def quality_from_release_name(release: str) -> str | None:
    """Dotted filename on a link row; resolution may sit before a ``-GROUP`` suffix."""
    return _quality_from_scan(release)


def canon_language(token: str) -> str | None:
    meta = _meta_tokens(token)
    for alias, name in _LANGUAGE:
        if _scoped_has(meta, alias):
            return name
    from mediatracker.core.quality_profile import _has_contextual_ca

    if _has_contextual_ca(token or ""):
        return "VFQ"
    return None


def _node_by_id(root: Node, node_id: str) -> Node | None:
    for node in root.find_all("div"):
        if (node.attrs.get("id") or "") == node_id:
            return node
    return None


def _year_from_année_item(item: Node) -> int | None:
    text = item.text().strip()
    folded = text.lower()
    if not folded.startswith("année"):
        return None
    for anchor in item.find_all("a"):
        href = anchor.attrs.get("href") or ""
        found = _YEAR_LINK.search(href)
        if found:
            return int(found.group(1))
    bold = item.find("b")
    blob = bold.text() if bold is not None else text
    found = _BLOCK_YEAR.search(blob)
    return int(found.group(1)) if found else None


def _year_from_detail_lists(scope: Node) -> int | None:
    for list_root in (*scope.find_all("div", "wa-post-detail-list"), *scope.find_all("ul")):
        classes = list_root.classes() if list_root.tag == "ul" else set()
        if list_root.tag == "ul" and "detail-list" not in classes:
            continue
        for item in list_root.find_all("li"):
            year = _year_from_année_item(item)
            if year is not None:
                return year
    return None


def _block_year(block: Node) -> int | None:
    return _year_from_detail_lists(block)


def _page_year(root: Node) -> int | None:
    scope = _node_by_id(root, "detail-page")
    if scope is None:
        for node in root.find_all("div"):
            if "wa-mid-blocks" in node.classes():
                scope = node
                break
    if scope is None:
        return None
    return _year_from_detail_lists(scope)


def parse_heading(raw: str) -> Heading:
    text = _PREFIX.sub("", (raw or "").strip())
    text = re.sub(r"(?i)\s*en (téléchargement|telechargement|streaming) sur wawa\s*city", " ", text)
    year = None
    found_year = _YEAR.search(text)
    if found_year:
        year = int(found_year.group(1))
        text = f"{text[: found_year.start()]} {text[found_year.end() :]}"
    _TAG.findall(text)
    text = _TAG.sub(" ", text)
    season = None
    episode_season, episode_number = _episode_bits(text)
    if episode_season is not None or episode_number is not None:
        text = _EPISODE.sub(" ", text)
    found_season = _SEASON_TEXT.search(text)
    if found_season:
        season = int(found_season.group(1))
        text = f"{text[: found_season.start()]} {text[found_season.end() :]}"
    if season is None:
        season = episode_season
    title = re.sub(r"\s+", " ", text).strip(" -–—:|")
    title = re.sub(r"(?i)\s*[-–—|]\s*french\s*$", "", title)
    title = re.sub(
        r"(?i)(?:\s*[-–—|]\s*)?\b(?:truefrench|vostfr|vff|vf2|vfq|multi|french|vf|webrrip|webrip|web-dl|webdl)\b",
        " ",
        title,
    )
    title = re.sub(
        r"(?i)\s*\(\s*(?:french|truefrench|vostfr|vff|vf2|vfq|multi|vf|ca)\s*\)\s*",
        " ",
        title,
    )
    title = re.sub(r"\s*\(\s*\)\s*", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -–—:|")
    title, _version = split_release_title(title)
    quality = canon_quality(raw)
    language = canon_language(raw)
    media_type = _media_type(raw)
    return Heading(title, year, season, quality, language, media_type)


def _first_srcset_url(raw: str) -> str | None:
    for part in raw.split(","):
        candidate = part.strip().split(None, 1)[0] if part.strip() else ""
        if candidate and not candidate.startswith("data:"):
            return candidate
    return None


def _pick_poster_src(image) -> str | None:
    ordered: list[str] = []
    for attr in ("data-src", "data-lazy-src"):
        value = (image.attrs.get(attr) or "").strip()
        if value:
            ordered.append(value)
    for attr in ("srcset", "data-srcset"):
        value = image.attrs.get(attr)
        if value:
            picked = _first_srcset_url(str(value))
            if picked:
                ordered.append(picked)
    src = (image.attrs.get("src") or "").strip()
    if src:
        ordered.append(src)
    for candidate in ordered:
        text = candidate.strip()
        if not text or text.startswith("data:"):
            continue
        lowered = text.lower()
        if lowered.endswith("blank.gif") or lowered.endswith("/blank.gif"):
            continue
        return text
    return None


def _block_poster(block: Node, base_url: str) -> str | None:
    cover = block.find("div", "wa-sub-block-cover")
    if cover is None:
        return None
    image = cover.find("img")
    if image is None:
        return None
    src = _pick_poster_src(image)
    if not src:
        return None
    if src.startswith(("http://", "https://")):
        parsed = urlparse(src)
        path = parsed.path or ""
        return path if path.startswith("/") else None
    if src.startswith("/"):
        return src
    joined = urljoin(base_url.rstrip("/") + "/", src)
    parsed = urlparse(joined)
    path = parsed.path or ""
    return path if path.startswith("/") else None


def parse_search(html: str, base_url: str) -> list[SearchHit]:
    root = parse_html(html)
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for block in root.find_all("div", "wa-sub-block"):
        title_node = block.find("div", "wa-sub-block-title")
        if title_node is None:
            continue
        link = title_node.find("a")
        if link is None:
            continue
        href = (link.attrs.get("href") or "").strip()
        if not href:
            continue
        url = urljoin(base_url.rstrip("/") + "/", href)
        if url in seen:
            continue
        seen.add(url)
        raw = link.text() or title_node.text()
        heading = parse_heading(raw)
        year = heading.year if heading.year is not None else _block_year(block)
        season = heading.season if heading.season is not None else season_from_url(url)
        hits.append(
            SearchHit(
                title=heading.title,
                year=year,
                media_type=heading.media_type,
                season=season,
                quality=heading.quality,
                language=heading.language,
                url=url,
                rejection=_cam_reason(raw),
                poster_url=_block_poster(block, base_url),
                profile_hint=raw,
            )
        )
    return hits


# The v1 discovery page listed about thirty blocks. A cap keeps a noisy homepage from flooding the catalog.
_LISTING_CAP = 60


def parse_listing(html: str, base_url: str, media_type: str) -> list[SearchHit]:
    """Latest-releases blocks (``div.wa-sub-block``), films and series pages alike.

    The category of the page wins when the heading has no « Films » / « Séries » prefix.
    """
    root = parse_html(html)
    hits: list[SearchHit] = []
    seen: set[str] = set()
    kind = media_type if media_type in {"film", "series"} else "film"
    blocks = root.find_all("div", "wa-sub-block")

    def append_hit(block: Node | None, title_node: Node) -> None:
        if len(hits) >= _LISTING_CAP:
            return
        link = title_node.find("a")
        if link is None:
            return
        href = (link.attrs.get("href") or "").strip()
        if not href or href.startswith("#"):
            return
        url = urljoin(base_url.rstrip("/") + "/", href)
        if url in seen:
            return
        seen.add(url)
        raw = link.text() or title_node.text()
        heading = parse_heading(raw)
        year = heading.year
        if year is None and block is not None:
            year = _block_year(block)
        season_from_text, episode = _episode_bits(raw)
        season = heading.season
        if season is None:
            season = season_from_text if season_from_text is not None else season_from_url(url)
        listed = heading.media_type if heading.media_type in {"film", "series"} else kind
        poster = _block_poster(block, base_url) if block is not None else None
        hits.append(
            SearchHit(
                title=heading.title,
                year=year,
                media_type=listed,
                season=season,
                quality=heading.quality,
                language=heading.language,
                url=url,
                episode=episode,
                rejection=_cam_reason(raw),
                poster_url=poster,
                profile_hint=raw,
            )
        )

    for block in blocks:
        title_node = block.find("div", "wa-sub-block-title")
        if title_node is None:
            continue
        append_hit(block, title_node)
    for title_node in root.find_all("div", "wa-sub-block-title"):
        append_hit(None, title_node)
    return hits


def _episode_bits(text: str) -> tuple[int | None, int | None]:
    return episode_bits_from_text(text or "")


def parse_links(html: str, page_url: str) -> tuple[Heading, list[PageLink]]:
    root = parse_html(html)
    h1 = root.find("h1")
    h1_text = h1.text() if h1 is not None else ""
    heading = parse_heading(h1_text)
    year = heading.year if heading.year is not None else _page_year(root)
    meta_quality, meta_language = _page_meta(root)
    quality = meta_quality or heading.quality
    language = meta_language or heading.language
    season = heading.season if heading.season is not None else season_from_url(page_url)
    heading = Heading(
        heading.title,
        year,
        season,
        quality,
        language,
        heading.media_type,
        profile_hint=h1_text,
    )
    links = _rows(root, page_url, season, quality, language)
    return heading, links


def parse_alternate_qualities(
    html: str,
    base_url: str,
    *,
    page_title: str,
    page_year: int | None,
) -> list[AlternateQuality]:
    """Sister pages from the « Autres qualités » block (button labels = qualité/langue)."""
    root = parse_html(html)
    alts: list[AlternateQuality] = []
    seen: set[str] = set()
    for block in root.find_all("div", "wa-sub-block"):
        title_node = block.find("div", "wa-sub-block-title")
        if title_node is None or not re.search(r"(?i)autres\s+qualit", title_node.text()):
            continue
        for ul in block.find_all("ul", "wa-post-list-ofLinks"):
            for li in ul.find_all("li"):
                anchor = li.find("a")
                if anchor is None:
                    continue
                href = (anchor.attrs.get("href") or "").strip()
                if not href:
                    continue
                url = urljoin(base_url.rstrip("/") + "/", href)
                if url in seen:
                    continue
                seen.add(url)
                button = anchor.find("button")
                label = button.text() if button is not None else anchor.text()
                alts.append(
                    _alternate_quality_from_label(
                        label,
                        url=url,
                        title=page_title,
                        year=page_year,
                    )
                )
        if alts:
            break
    return alts


def _alternate_quality_from_label(
    label: str,
    *,
    url: str,
    title: str,
    year: int | None,
) -> AlternateQuality:
    """Read quality and language from the button; title and year come from the open fiche."""
    quality = canon_quality(label)
    language = canon_language(label)
    if language is None and re.search(r"(?i)\bmulti\b", label):
        language = "MULTI"
    return AlternateQuality(
        title=title,
        year=year,
        quality=quality,
        language=language,
        url=url,
        rejection=_cam_reason(label),
        label=label,
    )


def season_from_url(url: str) -> int | None:
    path = urlparse(url).path
    found = _SEASON_URL.search(path)
    if not found:
        return None
    raw = found.group(1) or found.group(2)
    return int(raw) if raw else None


def _rows(
    root: Node,
    page_url: str,
    page_season: int | None,
    quality: str | None,
    language: str | None,
) -> list[PageLink]:
    current_season = page_season
    current_episode: int | None = None
    links: list[PageLink] = []
    seen: set[tuple[str, str, int | None, int | None]] = set()
    for row in root.find_all("tr"):
        classes = row.classes()
        if "title" in classes or "episode-title" in classes:
            current_season, current_episode = _episode_heading(row.text(), page_season)
            continue
        anchor = _download_anchor(row)
        if anchor is None:
            continue
        href = urljoin(page_url, _absolute(anchor.attrs.get("href") or ""))
        if not _protected(href):
            continue
        cells = [cell.text() for cell in row.find_all("td")]
        row_text = row.text()
        hoster = _hoster(cells, row_text)
        if hoster is None:
            continue
        key = (href, hoster, current_season, current_episode)
        if key in seen:
            continue
        seen.add(key)
        release = release_name_from_row(row_text)
        links.append(
            PageLink(
                url=href,
                hoster=hoster,
                season=current_season,
                episode=current_episode,
                quality=quality_from_release_name(release) or quality if release else quality,
                language=_release_language(release, language),
                filename=release,
            )
        )
    return links


def _absolute(href: str) -> str:
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    return href


def _protected(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    blob = f"{host} {url.lower()}"
    return any(marker in blob for marker in _PROTECT)


def _download_anchor(row: Node) -> Node | None:
    fallback: Node | None = None
    for anchor in row.find_all("a"):
        label = anchor.text().lower()
        href = (anchor.attrs.get("href") or "").strip()
        if not href or "regarder" in label or "stream" in label:
            continue
        if _protected(_absolute(href)):
            return anchor
        if fallback is None:
            fallback = anchor
    return fallback


def _episode_heading(text: str, page_season: int | None) -> tuple[int | None, int | None]:
    found = _EPISODE.search(text)
    if not found:
        return page_season, None
    if found.group(1) and found.group(2):
        return int(found.group(1)), int(found.group(2))
    if found.group(3) and found.group(4):
        return int(found.group(3)), int(found.group(4))
    episode = int(found.group(5))
    return page_season, episode


_RELEASE_TOKEN = re.compile(
    r"(?i)([A-Za-z0-9][A-Za-z0-9.]*"
    r"(?:1080p|720p|2160p|480p|webrip|web-dl|bluray|multi|vf2)"
    r"[A-Za-z0-9.-]*"
    r"(?:\.(?:mkv|mp4|avi|m4v))?)"
)


def _release_language(release: str | None, page_language: str | None) -> str | None:
    if not release:
        return page_language
    from mediatracker.core.quality_profile import detect_language

    from_name = detect_language(release)
    return from_name or canon_language(release) or page_language


def release_name_from_row(row_text: str) -> str | None:
    """The dotted release name Wawacity prints on a link row, when it has one.

    ``Lien 1: Practical.Magic.2.2026.MULTi.VF2.1080p.WEBRip....mkv | Uploady | 6.6 Go``
    keeps the filename and drops the hoster and the size.
    """
    found = _RELEASE_TOKEN.search(row_text or "")
    if not found:
        return None
    name = found.group(1).strip(" .|")
    if len(name) < 12 or "." not in name:
        return None
    return name[:500]


def _hoster(cells: list[str], row_text: str) -> str | None:
    for cell in cells:
        found = _one_hoster(cell)
        if found:
            return found
    return _one_hoster(row_text)


def _one_hoster(text: str) -> str | None:
    folded = text.lower()
    hits: list[str] = []
    for alias, name in _HOSTERS:
        if alias in folded and name not in hits:
            hits.append(name)
    if len(hits) == 1:
        return hits[0]
    return None


def _page_meta(root: Node) -> tuple[str | None, str | None]:
    quality = None
    language = None
    for item in root.find_all("li"):
        label = item.text().lower()
        bold = item.find("b")
        value = bold.text() if bold is not None else ""
        if language is None and label.startswith("langue"):
            language = canon_language(value)
        elif quality is None and (label.startswith("qualité") or label.startswith("qualite")):
            quality = quality_from_meta_value(value)
    return quality, language


def _cam_reason(raw: str) -> str | None:
    if _CAM.search(raw or ""):
        return "Qualité caméra ou TS exclue."
    return None


def _media_type(raw: str) -> str | None:
    folded = (raw or "").strip().lower()
    if folded.startswith("film"):
        return "film"
    if folded.startswith("série") or folded.startswith("serie") or folded.startswith("series"):
        return "series"
    return None
