"""Assemble user docs into docs/llms-full.md and docs/llms.txt (stdlib only)."""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"

EXCLUDE_NAMES = frozenset({"RAPPORT-DOC-UTILISATEUR.md", "README.md", "llms-full.md"})
EXCLUDE_DIRS = frozenset({"interne", "archives", "exemples"})

# Ordre logique des fichiers (tous les docs utilisateur inclus doivent y figurer).
ORDERED_REL_PATHS: tuple[str, ...] = (
    "installation.md",
    "architecture.md",
    "premier-demarrage.md",
    "telechargements.md",
    "reglages/README.md",
    "reglages/compte.md",
    "reglages/qualite.md",
    "reglages/sources.md",
    "reglages/scan.md",
    "reglages/jellyfin.md",
    "reglages/tmdb-catalog.md",
    "reglages/notifications.md",
    "reglages/corbeille.md",
    "reglages/alldebrid.md",
    "reglages/disque.md",
    "reglages/hive.md",
    "reglages/application.md",
    "reglages/maj.md",
    "reglages/jetons.md",
    "reglages/reset.md",
    "medias.md",
    "tmdb.md",
    "jellyfin.md",
    "hive.md",
    "home-assistant.md",
    "mises-a-jour.md",
    "sauvegarde.md",
    "depannage-faq.md",
)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
INLINE_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def discover_included_paths() -> set[str]:
    found: set[str] = set()
    for path in DOCS.glob("*.md"):
        if path.name in EXCLUDE_NAMES:
            continue
        found.add(path.relative_to(DOCS).as_posix())
    reglages = DOCS / "reglages"
    if reglages.is_dir():
        for path in reglages.glob("*.md"):
            found.add(path.relative_to(DOCS).as_posix())
    return found


def section_anchor(docs_rel: str) -> str:
    return "docs-" + docs_rel.replace("/", "-").removesuffix(".md").replace(".", "-")


def slugify_heading(title: str) -> str:
    title = re.sub(r"[`*_]", "", title.strip())
    title = unicodedata.normalize("NFKD", title)
    title = "".join(ch for ch in title if not unicodedata.combining(ch))
    title = title.lower()
    title = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE)
    title = re.sub(r"\s+", "-", title.strip())
    return title or "section"


def strip_leading_h1(text: str) -> tuple[str | None, str]:
    lines = text.splitlines()
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx < len(lines):
        match = HEADING_RE.match(lines[idx])
        if match and len(match.group(1)) == 1:
            title = match.group(2).strip()
            rest = "\n".join(lines[idx + 1 :])
            if rest and not rest.startswith("\n"):
                rest = "\n" + rest
            return title, rest.lstrip("\n")
    return None, text


def bump_headings(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match:
            level = len(match.group(1))
            if level < 6:
                out.append("#" * (level + 1) + " " + match.group(2))
            else:
                out.append(line)
        else:
            out.append(line)
    return "\n".join(out)


def heading_slug_map(docs_rel: str, text: str) -> dict[str, str]:
    """Map lowercased heading text -> slug (unique within file)."""
    slugs: dict[str, str] = {}
    counts: dict[str, int] = {}
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if not match:
            continue
        title = match.group(2).strip()
        base = slugify_heading(title)
        counts[base] = counts.get(base, 0) + 1
        slug = base if counts[base] == 1 else f"{base}-{counts[base]}"
        slugs[title.lower()] = slug
    return slugs


def resolve_doc_link(current_docs_rel: str, target: str) -> str | None:
    target = target.strip()
    if not target.endswith(".md"):
        return None
    if target.startswith("/"):
        return None
    base = (DOCS / current_docs_rel).parent
    resolved = (base / target).resolve()
    try:
        rel = resolved.relative_to(DOCS.resolve())
    except ValueError:
        return None
    rel_posix = rel.as_posix()
    included = discover_included_paths()
    if rel_posix not in included:
        return None
    return rel_posix


def rewrite_links(text: str, current_docs_rel: str, file_heading_slugs: dict[str, dict[str, str]]) -> str:
    def replace_link(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        if url.startswith(("http://", "https://", "mailto:")):
            return match.group(0)
        fragment = ""
        path_part = url
        if "#" in url:
            path_part, frag = url.split("#", 1)
            fragment = frag.strip()
        if not path_part:
            if fragment:
                slugs = file_heading_slugs.get(current_docs_rel, {})
                slug = slugs.get(fragment.lower()) or slugify_heading(fragment)
                return f"[{label}](#{slug})"
            return match.group(0)
        target_rel = resolve_doc_link(current_docs_rel, path_part)
        if target_rel is None:
            return match.group(0)
        anchor = section_anchor(target_rel)
        if fragment:
            other_slugs = file_heading_slugs.get(target_rel, {})
            slug = other_slugs.get(fragment.lower()) or slugify_heading(fragment)
            return f"[{label}](#{anchor}-{slug})"
        return f"[{label}](#{anchor})"

    return INLINE_LINK_RE.sub(replace_link, text)


def build_header() -> str:
    return """\
# MediaTracker — documentation pour assistants IA

MediaTracker est une application **mono-utilisateur** pour NAS (souvent OpenMediaVault) : suivre des films et
séries via TMDB, chercher des liens sur des sources configurées, télécharger via **AllDebrid** et **aria2** (RPC
interne au conteneur), ranger les fichiers sur le disque et les exposer à **Jellyfin** (optionnel). Notifications
via webhook (ex. Home Assistant). Interface web en français, thème sombre ; déploiement typique en **une image
Docker** (`ghcr.io/nico2511/mediatracker-hive`).

**Pile technique :** API **FastAPI** (Python 3.12+), base **SQLite**, workers et intégrations dans `backend/` ;
interface **Vue** dans `frontend/` ; conteneur unique avec aria2 non exposé sur le réseau.

**Parcours d’un téléchargement (résumé) :** un scan ou une action manuelle interroge les **sources** dans l’ordre
configuré ; le lien retenu respecte le **profil de qualité** et les préférences d’hébergeurs ; **AllDebrid** débloque
le lien ; **aria2** télécharge en local ; à la fin, le fichier est **déplacé** vers Films ou Séries (même système de
fichiers que les téléchargements), la bibliothèque est rescannée et Jellyfin peut être notifié.

Ce fichier est généré automatiquement à partir de `docs/` (hors interne, archives et exemples). Régénération :
`python scripts/build_ai_doc.py`.
"""


def section_title(docs_rel: str, stripped_h1: str | None) -> str:
    if stripped_h1:
        return stripped_h1
    stem = Path(docs_rel).stem
    if stem == "README":
        return "Réglages (index)"
    return stem.replace("-", " ").capitalize()


def one_line_summary(docs_rel: str, raw: str) -> str:
    """Première phrase utile après le titre, pour llms.txt."""
    _, body = strip_leading_h1(raw)
    in_fence = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped or stripped.startswith(("#", "|", "-", "*", "[")):
            continue
        if stripped.lower().startswith(("flowchart", "graph ", "subgraph", "sequencediagram")):
            continue
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", stripped)
        line = re.sub(r"[*_`]", "", line)
        if line:
            return line[:200]
    return section_title(docs_rel, None)


def assemble() -> tuple[str, str]:
    included = discover_included_paths()
    ordered = [p for p in ORDERED_REL_PATHS if p in included]
    missing_order = included - set(ORDERED_REL_PATHS)
    if missing_order:
        raise SystemExit(f"Fichiers docs non listés dans ORDERED_REL_PATHS : {sorted(missing_order)}")
    extra_order = [p for p in ORDERED_REL_PATHS if p not in included]
    if extra_order:
        raise SystemExit(f"Fichiers ORDERED_REL_PATHS absents du disque : {extra_order}")

    raw_by_path: dict[str, str] = {}
    heading_slugs: dict[str, dict[str, str]] = {}
    for docs_rel in ordered:
        raw = (DOCS / docs_rel).read_text(encoding="utf-8")
        raw_by_path[docs_rel] = raw
        heading_slugs[docs_rel] = heading_slug_map(docs_rel, raw)

    parts = [build_header().rstrip(), ""]

    toc_lines = ["## Sommaire", ""]
    for docs_rel in ordered:
        h1, _ = strip_leading_h1(raw_by_path[docs_rel])
        title = section_title(docs_rel, h1)
        anchor = section_anchor(docs_rel)
        toc_lines.append(f"- [{title}](#{anchor}) — `docs/{docs_rel}`")
    parts.append("\n".join(toc_lines))
    parts.append("")

    for docs_rel in ordered:
        raw = raw_by_path[docs_rel]
        h1, body = strip_leading_h1(raw)
        title = section_title(docs_rel, h1)
        anchor = section_anchor(docs_rel)
        body = bump_headings(body)
        body = rewrite_links(body, docs_rel, heading_slugs)
        parts.append(f"## {title}")
        parts.append("")
        parts.append(f"Chemin source : `docs/{docs_rel}`")
        parts.append("")
        parts.append(f'<a id="{anchor}"></a>')
        parts.append("")
        if body.strip():
            parts.append(body.rstrip())
            parts.append("")

    llms_full = "\n".join(parts).rstrip() + "\n"

    llms_txt_lines = [
        "# MediaTracker",
        "> Documentation utilisateur assemblée pour assistants IA (installation, réglages, intégrations).",
        "",
        "## Sections",
    ]
    for docs_rel in ordered:
        summary = one_line_summary(docs_rel, raw_by_path[docs_rel])
        title = section_title(docs_rel, strip_leading_h1(raw_by_path[docs_rel])[0])
        llms_txt_lines.append(f"- docs/{docs_rel} — {title} — {summary}")
    llms_txt = "\n".join(llms_txt_lines).rstrip() + "\n"

    return llms_full, llms_txt


def write_outputs() -> None:
    llms_full, llms_txt = assemble()
    (DOCS / "llms-full.md").write_text(llms_full, encoding="utf-8")
    (DOCS / "llms.txt").write_text(llms_txt, encoding="utf-8")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        llms_full, llms_txt = assemble()
        full_path = DOCS / "llms-full.md"
        txt_path = DOCS / "llms.txt"
        ok = True
        if not full_path.exists() or full_path.read_text(encoding="utf-8") != llms_full:
            ok = False
        if not txt_path.exists() or txt_path.read_text(encoding="utf-8") != llms_txt:
            ok = False
        return 0 if ok else 1
    write_outputs()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
