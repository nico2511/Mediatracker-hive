#!/usr/bin/env python3
"""Référence (vérité terrain) de l'import d'historique Jellyfin 10.11 -> MediaTracker.

Script autonome (bibliothèque standard uniquement), indépendant du code de l'application.

Entrée : une base `jellyfin.db` (SQLite, schéma EF Core 10.11) OU le ZIP de sauvegarde 10.11
(`Database/*.json`). Les deux doivent produire exactement la même sortie.

Sortie : la liste canonique des éléments (films / épisodes) VUS par utilisateur, une ligne par
élément unique, avec ids externes, date, source et niveau de confiance, plus des statistiques.

Usage :
    python3 reference.py SOURCE [--csv sortie.csv] [--stats stats.json]
    python3 reference.py SOURCE_A --compare SOURCE_B      # vérifie 0 écart SQLite / ZIP

Aucune écriture dans SOURCE : SQLite est ouverte en lecture seule (mode=ro, immutable).
Aucun appel réseau : les séries sans titre sont simplement signalées « TMDB /find requis ».

Principes (approche conservatrice) :
  * on n'invente rien : un élément ambigu est listé « non importable » avec sa raison ;
  * un élément = un film (tmdb/imdb) ou un épisode (série + saison + épisode) ;
  * sources : UserData rattachée à BaseItems (bibliothèque actuelle), UserData détachée
    (médias supprimés, ItemId = placeholder ...0001), journal ActivityLogs VideoPlayback
    (démarrage) et VideoPlaybackStopped (arrêt) ;
  * le journal sert d'abord à TITRER les éléments UserData et à prouver les doublons ; il ne crée
    une entrée seule que si aucune ligne UserData ne correspond, et cette entrée n'est jamais « sûre ».

Étapes : load -> normalize -> build (lots UserData) -> associate_logs -> finalize
         (séries, fusion des doublons, titres, confiance) -> series_checks -> resolve_guid_only
         -> journal_only -> write_csv / compute_stats.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import re
import sqlite3
import sys
import unicodedata
import zipfile

PLACEHOLDER_ID = "00000000000000000000000000000001"
TYPE_MOVIE = "MediaBrowser.Controller.Entities.Movies.Movie"
TYPE_SERIES = "MediaBrowser.Controller.Entities.TV.Series"
TYPE_EPISODE = "MediaBrowser.Controller.Entities.TV.Episode"

# Fenêtre « arrêt » demandée initialement (0 <= arrêt - LastPlayedDate <= durée, sinon 3 h) :
# conservée pour mémoire et pour la mesure --stats, mais NON utilisée (48 % d'erreurs mesurées).
WINDOW_BEFORE_S = 60
WINDOW_MAX_S = 3 * 3600

CONF_SURE = "sûr"
CONF_PROBABLE = "probable"
CONF_NO = "non importable"

SRC_ITEMS = "items+userdata"
SRC_DELETED = "userdata seul (supprimé)"
SRC_LOG = "journal"

GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")
SXXEYY_RE = re.compile(r"(?<![A-Za-z0-9])[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})(?!\d)")


# --------------------------------------------------------------------------- normalisation

def norm_guid(value) -> str:
    """GUID quelconque (MAJ/min, avec/sans tirets) -> 32 hex minuscules ; '' sinon."""
    if not value:
        return ""
    s = str(value).strip()
    if not GUID_RE.match(s):
        return ""
    return s.replace("-", "").lower()


def norm_date(value) -> str:
    """Date SQLite 'YYYY-MM-DD HH:MM:SS[.f]' ou ZIP 'YYYY-MM-DDTHH:MM:SS[.f]Z' (UTC)
    -> 'YYYY-MM-DDTHH:MM:SS.fffffffZ' (7 décimales, comparable comme chaîne)."""
    if not value:
        return ""
    s = str(value).strip().replace("T", " ").rstrip("Z")
    if "+" in s[10:]:
        s = s[: 10 + s[10:].index("+")]
    main, _, frac = s.partition(".")
    frac = (frac + "0000000")[:7]
    return main.replace(" ", "T") + "." + frac + "Z"


def to_seconds(iso: str) -> float:
    main, frac = iso.rstrip("Z").split(".")
    d = dt.datetime.fromisoformat(main).replace(tzinfo=dt.timezone.utc)
    return d.timestamp() + int(frac) / 1e7


def norm_title(title: str) -> str:
    """Forme de comparaison d'un titre (casse, accents, ponctuation ignorés)."""
    t = unicodedata.normalize("NFKD", title or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    t = re.sub(r"[^0-9a-z]+", " ", t).strip()
    return t


def as_int(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return int(v)
    return int(v)


# --------------------------------------------------------------------------- lecture des sources

TABLE_COLUMNS = {
    "Users": ["Id", "Username"],
    "BaseItems": ["Id", "Type", "Name", "OriginalTitle", "ProductionYear", "RunTimeTicks", "SeriesId",
                  "SeriesName", "IndexNumber", "ParentIndexNumber", "IsVirtualItem"],
    "BaseItemProviders": ["ItemId", "ProviderId", "ProviderValue"],
    "UserData": ["ItemId", "UserId", "CustomDataKey", "Played", "PlayCount", "LastPlayedDate",
                 "PlaybackPositionTicks", "RetentionDate"],
    "ActivityLogs": ["Id", "Name", "Type", "UserId", "ItemId", "DateCreated"],
}


def load_sqlite(path: str) -> dict:
    uri = "file:" + path + "?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    try:
        tables = {}
        for t, cols in TABLE_COLUMNS.items():
            sql = "SELECT " + ", ".join(f'"{c}"' for c in cols) + f' FROM "{t}"'
            if t == "ActivityLogs":
                sql += " WHERE \"Type\" IN ('VideoPlayback', 'VideoPlaybackStopped')"
            tables[t] = [dict(zip(cols, r, strict=False)) for r in con.execute(sql)]
        return tables
    finally:
        con.close()


def load_zip(path: str) -> dict:
    tables = {}
    with zipfile.ZipFile(path) as zf:
        names = {n for n in zf.namelist()}
        for t, cols in TABLE_COLUMNS.items():
            name = f"Database/{t}.json"
            if name not in names:
                raise SystemExit(f"ZIP invalide : {name} absent")
            data = json.loads(zf.read(name).decode("utf-8"))
            rows = [{c: r.get(c) for c in cols} for r in data]
            if t == "ActivityLogs":
                rows = [r for r in rows if r["Type"] in ("VideoPlayback", "VideoPlaybackStopped")]
            tables[t] = rows
    return tables


def load(path: str) -> dict:
    if zipfile.is_zipfile(path):
        raw = load_zip(path)
    else:
        raw = load_sqlite(path)
    return normalize(raw)


def normalize(raw: dict) -> dict:
    """Met les deux formats sous une forme unique (ids 32 hex minuscules, dates ISO 7 déc., int)."""
    t = {}
    t["users"] = {norm_guid(u["Id"]): u["Username"] for u in raw["Users"]}
    items = {}
    for r in raw["BaseItems"]:
        items[norm_guid(r["Id"])] = {
            "id": norm_guid(r["Id"]), "type": r["Type"], "name": r["Name"] or "",
            "year": as_int(r["ProductionYear"]), "runtime": as_int(r["RunTimeTicks"]),
            "series_id": norm_guid(r["SeriesId"]), "series_name": r["SeriesName"] or "",
            "index": as_int(r["IndexNumber"]), "parent_index": as_int(r["ParentIndexNumber"]),
        }
    t["items"] = items
    prov = collections.defaultdict(dict)
    for r in raw["BaseItemProviders"]:
        prov[norm_guid(r["ItemId"])][r["ProviderId"]] = str(r["ProviderValue"]).strip()
    t["providers"] = prov
    t["userdata"] = [{
        "item_id": norm_guid(r["ItemId"]), "user_id": norm_guid(r["UserId"]),
        "key": r["CustomDataKey"], "played": as_int(r["Played"]) or 0,
        "play_count": as_int(r["PlayCount"]) or 0, "last_played": norm_date(r["LastPlayedDate"]),
        "position": as_int(r["PlaybackPositionTicks"]) or 0, "retention": norm_date(r["RetentionDate"]),
    } for r in raw["UserData"]]
    t["logs"] = sorted(({
        "id": as_int(r["Id"]), "name": r["Name"] or "", "type": r["Type"], "user_id": norm_guid(r["UserId"]),
        "item_id": norm_guid(r["ItemId"]), "date": norm_date(r["DateCreated"]),
    } for r in raw["ActivityLogs"]), key=lambda x: x["id"])
    return t


# --------------------------------------------------------------------------- clés UserData

def classify_key(key: str):
    """CustomDataKey -> (genre, valeurs). Genres :
       guid            Id de l'élément lui-même
       series_guid_ep  GUID de la série + SSSEEE (série sans tvdb/imdb)
       tvdb_ep         tvdb série (>=2 chiffres) + SSSEEE  (8 chiffres et plus)
       imdb_ep         tt + imdb série (7-10 chiffres) + SSSEEE
       num_short       1 à 7 chiffres : tmdb de film OU tvdb à 1 chiffre + SSSEEE -> décidé par le contexte
       imdb_title      tt + 7 à 10 chiffres : imdb de film
       person / autre  ignorés"""
    k = (key or "").strip()
    if GUID_RE.match(k) and len(k) == 36:
        return "guid", {"guid": norm_guid(k)}
    m = re.fullmatch(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(\d{3})(\d{3})", k)
    if m:
        return "series_guid_ep", {"series_guid": norm_guid(m.group(1)), "s": int(m.group(2)), "e": int(m.group(3))}
    if re.fullmatch(r"tt\d+", k):
        n = len(k) - 2
        if 7 <= n <= 10:
            return "imdb_title", {"imdb": k}
        if 13 <= n <= 16:
            return "imdb_ep", {"imdb": k[:-6], "s": int(k[-6:-3]), "e": int(k[-3:])}
        return "autre", {}
    if re.fullmatch(r"\d+", k):
        if len(k) <= 7:
            return "num_short", {"value": k}
        return "tvdb_ep", {"tvdb": k[:-6].lstrip("0") or "0", "s": int(k[-6:-3]), "e": int(k[-3:])}
    if k.startswith("Person-"):
        return "person", {}
    return "autre", {}


# --------------------------------------------------------------------------- journal

PLAYBACK_TEMPLATES = (
    # (préfixe après le nom d'utilisateur, séparateur appareil, type d'événement)
    (" vient d'arrêter la lecture de ", " sur ", "VideoPlaybackStopped"),
    (" vient d’arrêter la lecture de ", " sur ", "VideoPlaybackStopped"),
    (" has finished playing ", " on ", "VideoPlaybackStopped"),
    (" est en train de lire ", " sur ", "VideoPlayback"),
    (" is playing ", " on ", "VideoPlayback"),
)


def parse_playback_message(name: str, username: str):
    """Texte d'un événement de lecture -> '<quoi>' (titre du film ou '<Série> - <Épisode>').
       FR : '<user> vient d'arrêter la lecture de <quoi> sur <appareil>' / '<user> est en train de lire …'
       EN : '<user> has finished playing <quoi> on <appareil>' / '<user> is playing …'
       On coupe sur le DERNIER ' sur ' / ' on ' (des titres contiennent « sur »). None si non reconnu."""
    for prefix, sep, _ in PLAYBACK_TEMPLATES:
        head = username + prefix
        if name.startswith(head):
            rest = name[len(head):]
            i = rest.rfind(sep)
            if i <= 0:
                return None
            return rest[:i].strip()
    return None


def series_prefixes(what: str):
    """Découpages possibles '<Série> - <Épisode>' (un par ' - ')."""
    out = []
    start = 0
    while True:
        i = what.find(" - ", start)
        if i < 0:
            break
        out.append((what[:i].strip(), what[i + 3:].strip()))
        start = i + 1
    return out


def sxxeyy(text: str):
    found = {(int(a), int(b)) for a, b in SXXEYY_RE.findall(text or "")}
    return found.pop() if len(found) == 1 else None


# --------------------------------------------------------------------------- construction des éléments

def series_ids_of(items, providers, series_id):
    p = providers.get(series_id, {})
    return p.get("Tvdb", ""), p.get("Imdb", ""), p.get("Tmdb", "")


def build(t: dict, stats: collections.Counter):
    items, providers = t["items"], t["providers"]
    elements = []
    unassigned_guids = []          # (user, guid, raison)

    # Index des séries de la bibliothèque par id fournisseur (pour titrer les supprimés)
    lib_series = [i for i in items.values() if i["type"] == TYPE_SERIES]
    series_by_tvdb = collections.defaultdict(set)
    series_by_imdb = collections.defaultdict(set)
    for s in lib_series:
        tv, im, _ = series_ids_of(items, providers, s["id"])
        if tv:
            series_by_tvdb[tv].add(s["id"])
        if im:
            series_by_imdb[im].add(s["id"])
    movies_by_tmdb = collections.defaultdict(set)
    movies_by_imdb = collections.defaultdict(set)
    for m in items.values():
        if m["type"] == TYPE_MOVIE:
            p = providers.get(m["id"], {})
            if p.get("Tmdb"):
                movies_by_tmdb[p["Tmdb"]].add(m["id"])
            if p.get("Imdb"):
                movies_by_imdb[p["Imdb"]].add(m["id"])

    for uid in sorted(t["users"]):
        rows = [r for r in t["userdata"] if r["user_id"] == uid]
        stats["userdata_lignes"] += len(rows)

        # 1) Regroupement : lignes d'un même média = même LastPlayedDate / Played / PlayCount.
        #    Les lignes rattachées forment directement un « slot » (l'élément est connu).
        groups = collections.defaultdict(list)
        for r in rows:
            if r["last_played"]:
                gk = (r["last_played"], r["played"], r["play_count"])
            else:
                ph_id = r["item_id"] if r["item_id"] != PLACEHOLDER_ID else ""
                gk = ("", r["played"], r["play_count"], r["retention"], ph_id)
            groups[gk].append(r)

        for gk in sorted(groups):
            g = groups[gk]
            last_played, played, play_count = gk[0], gk[1], gk[2]
            slots = []          # dicts décrivant les éléments du lot
            guids = []          # guid détachés du lot (Id d'élément supprimé)
            attached = collections.defaultdict(list)
            detached_keys = []
            for r in g:
                if r["item_id"] and r["item_id"] != PLACEHOLDER_ID:
                    attached[r["item_id"]].append(r)
                else:
                    detached_keys.append(r)

            # 1a) éléments encore en bibliothèque
            for iid, _rs in sorted(attached.items()):
                it = items.get(iid)
                if not it or it["type"] not in (TYPE_MOVIE, TYPE_EPISODE):
                    continue
                if it["type"] == TYPE_MOVIE:
                    p = providers.get(iid, {})
                    slots.append({"kind": "film", "tmdb": p.get("Tmdb", ""), "imdb": p.get("Imdb", ""), "tvdb": "",
                                  "film_title": it["name"], "jf_ids": [iid], "current": iid, "source": SRC_ITEMS,
                                  "title_origin": "bibliothèque"})
                else:
                    sid = it["series_id"]
                    tv, im, tm = series_ids_of(items, providers, sid)
                    st = (items.get(sid) or {}).get("name") or it["series_name"]
                    slots.append({"kind": "episode", "tvdb": tv, "imdb": im, "tmdb": tm,
                                  "series_guid": sid if not (tv or im) else "",
                                  "series_title": st, "episode_title": it["name"],
                                  "s": it["parent_index"], "e": it["index"], "jf_ids": [iid], "current": iid,
                                  "source": SRC_ITEMS, "title_origin": "bibliothèque"})

            # 1b) clés détachées : classement
            ep_keys = collections.defaultdict(lambda: {"tvdb": [], "imdb": [], "sguid": []})
            film_tmdb, film_imdb = [], []
            for r in detached_keys:
                kind, v = classify_key(r["key"])
                if kind == "guid":
                    guids.append(v["guid"])
                elif kind == "tvdb_ep":
                    ep_keys[(v["s"], v["e"])]["tvdb"].append(v["tvdb"])
                elif kind == "imdb_ep":
                    ep_keys[(v["s"], v["e"])]["imdb"].append(v["imdb"])
                elif kind == "series_guid_ep":
                    ep_keys[(v["s"], v["e"])]["sguid"].append(v["series_guid"])
                elif kind == "num_short":
                    film_tmdb.append(v["value"])
                elif kind == "imdb_title":
                    film_imdb.append(v["imdb"])
                else:
                    stats["cle_ignoree_" + kind] += 1

            # Les clés d'un élément ENCORE en bibliothèque peuvent aussi exister détachées (ancienne vie) :
            # on retire du lot les clés déjà couvertes par un slot rattaché.
            for sl in slots:
                if sl["kind"] == "episode" and (sl["s"], sl["e"]) in ep_keys:
                    ek = ep_keys[(sl["s"], sl["e"])]
                    if sl["tvdb"] in ek["tvdb"]:
                        ek["tvdb"].remove(sl["tvdb"])
                    if sl["imdb"] in ek["imdb"]:
                        ek["imdb"].remove(sl["imdb"])
                    if sl["series_guid"] in ek["sguid"]:
                        ek["sguid"].remove(sl["series_guid"])
                if sl["kind"] == "film":
                    if sl["tmdb"] in film_tmdb:
                        film_tmdb.remove(sl["tmdb"])
                    if sl["imdb"] in film_imdb:
                        film_imdb.remove(sl["imdb"])

            # 1c) épisodes supprimés : un slot par (S,E) si l'appariement tvdb/imdb/sguid est univoque
            for (s, e), ek in sorted(ep_keys.items()):
                tvs, ims, sgs = ek["tvdb"], ek["imdb"], ek["sguid"]
                if not (tvs or ims or sgs):
                    continue
                n_series = max(len(tvs), len(ims)) + len(sgs)
                if len(tvs) <= 1 and len(ims) <= 1 and (len(sgs) == 0 or not (tvs or ims)) and len(sgs) <= 1:
                    slots.append({"kind": "episode", "tvdb": tvs[0] if tvs else "", "imdb": ims[0] if ims else "",
                                  "tmdb": "", "series_guid": sgs[0] if sgs else "", "series_title": "",
                                  "episode_title": "", "s": s, "e": e, "jf_ids": [], "current": "",
                                  "source": SRC_DELETED, "title_origin": ""})
                else:
                    # plusieurs séries ont le même S/E dans ce lot : appariement tvdb<->imdb à résoudre plus tard
                    slots.append({"kind": "episode_ambigu", "tvdbs": tvs, "imdbs": ims, "sguids": sgs,
                                  "s": s, "e": e, "jf_ids": [], "current": "", "source": SRC_DELETED})
                    stats["lots_se_multiseries"] += 1
                    _ = n_series

            # 1d) films supprimés : (tmdb, imdb) appariés si univoque
            if film_tmdb or film_imdb:
                if len(film_tmdb) <= 1 and len(film_imdb) <= 1:
                    slots.append({"kind": "film", "tmdb": film_tmdb[0] if film_tmdb else "",
                                  "imdb": film_imdb[0] if film_imdb else "", "tvdb": "", "film_title": "",
                                  "jf_ids": [], "current": "", "source": SRC_DELETED, "title_origin": ""})
                else:
                    slots.append({"kind": "film_ambigu", "tmdbs": film_tmdb, "imdbs": film_imdb,
                                  "jf_ids": [], "current": "", "source": SRC_DELETED})
                    stats["lots_films_multiples"] += 1

            # 1e) attribution des GUID détachés (Id d'anciennes vies de l'élément)
            if guids:
                real_slots = [sl for sl in slots]
                if len(real_slots) == 1:
                    real_slots[0]["jf_ids"].extend(sorted(guids))
                elif not real_slots:
                    for gid in sorted(guids):
                        slots.append({"kind": "guid_seul", "jf_ids": [gid], "current": "", "source": SRC_DELETED})
                else:
                    for gid in sorted(guids):
                        unassigned_guids.append((uid, gid, last_played, played, len(real_slots)))

            for sl in slots:
                sl["user_id"] = uid
                sl["last_played"] = last_played
                sl["played"] = played
                sl["play_count"] = play_count
                elements.append(sl)
    return elements, unassigned_guids


# --------------------------------------------------------------------------- rapprochement journal

START_BEFORE_S = 2       # VideoPlayback = LastPlayedDate à la seconde près (mesuré : 94 % à 0 s)
START_AFTER_S = 2


def associate_logs(t: dict, elements: list, stats: collections.Counter):
    """Rattache les événements de lecture (VideoPlayback / VideoPlaybackStopped) aux éléments UserData.

    Règles, dans l'ordre (un événement n'est rattaché qu'une fois) :
      1. ItemId (32 hex) = un Id Jellyfin connu de l'élément (bibliothèque ou GUID détaché) ;
      2. VideoPlayback dont l'heure = LastPlayedDate de l'élément (±2 s), élément unique à
         cette date (sinon S/E « SxxEyy » du texte doit désigner un seul élément) ;
      3. (fenêtre d'arrêt : écartée, 48 % d'erreurs mesurées) ;
      4. même texte '<quoi>' qu'un événement déjà rattaché à un seul élément (autres séances du même média).
    Renvoie (events, by_element) ; chaque event reçoit 'element' (indice) et 'rule'."""
    users = t["users"]
    events = []
    for log_row in t["logs"]:
        uname = users.get(log_row["user_id"])
        if uname is None:
            continue
        what = parse_playback_message(log_row["name"], uname)
        if what is None:
            stats["journal_non_parse"] += 1
            continue
        events.append(
            {
                "id": log_row["id"],
                "type": log_row["type"],
                "user_id": log_row["user_id"],
                "item_id": log_row["item_id"],
                "date": log_row["date"],
                "ts": to_seconds(log_row["date"]),
                "what": what,
                "key": (log_row["user_id"], norm_title(what)),
                "se": sxxeyy(what),
                "element": None,
                "rule": "",
            }
        )

    by_jf = {}
    for i, el in enumerate(elements):
        for j in el["jf_ids"]:
            by_jf[(el["user_id"], j)] = i

    def se_ok(ev, el):
        if ev["se"] is None or el.get("s") is None:
            return True
        return ev["se"] == (el["s"], el["e"])

    # règle 1
    for ev in events:
        if ev["item_id"]:
            i = by_jf.get((ev["user_id"], ev["item_id"]))
            if i is not None:
                ev["element"], ev["rule"] = i, "1-itemid"
            else:
                stats["journal_itemid_inconnu"] += 1

    # index temporel des éléments par utilisateur
    timeline = collections.defaultdict(list)
    for i, el in enumerate(elements):
        if el["last_played"]:
            timeline[el["user_id"]].append((to_seconds(el["last_played"]), i))
    for v in timeline.values():
        v.sort()
    import bisect

    def at_time(uid, lo, hi):
        tl = timeline.get(uid, [])
        a = bisect.bisect_left(tl, (lo, -1))
        b = bisect.bisect_right(tl, (hi, len(elements)))
        return tl[a:b]

    # règle 2 : démarrage = LastPlayedDate
    for ev in events:
        if ev["element"] is not None or ev["type"] != "VideoPlayback":
            continue
        cands = at_time(ev["user_id"], ev["ts"] - START_AFTER_S, ev["ts"] + START_BEFORE_S)
        idx = sorted({i for _, i in cands if se_ok(ev, elements[i])})
        if len(idx) == 1:
            ev["element"], ev["rule"] = idx[0], "2-demarrage"
        elif len(idx) > 1:
            stats["journal_demarrage_ambigu"] += 1

    # (fenêtre « 0 <= arrêt - LastPlayedDate <= durée/3 h » : ÉCARTÉE. Mesurée sur les 988 arrêts dont
    #  l'ItemId est connu, elle désigne le mauvais élément dans 48 % des cas — la plupart des arrêts
    #  concernent une séance antérieure du même média ou un média interrompu. Voir specification-import.md.)

    # règle 4 : propagation par texte identique (même utilisateur), si le texte ne désigne qu'un élément
    text_to = collections.defaultdict(set)
    for ev in events:
        if ev["element"] is not None and ev["rule"] in ("1-itemid", "2-demarrage"):
            text_to[ev["key"]].add(identity_key(elements[ev["element"]]))
    text_elem = {}
    for ev in events:
        if ev["element"] is not None and ev["rule"] in ("1-itemid", "2-demarrage") and len(text_to[ev["key"]]) == 1:
            text_elem.setdefault(ev["key"], ev["element"])
    for ev in events:
        if ev["element"] is None and ev["key"] in text_elem:
            ev["element"], ev["rule"] = text_elem[ev["key"]], "4-texte"
    for ev in events:
        stats["journal_regle_" + (ev["rule"] or "aucune") + "_" + ev["type"]] += 1
    return events


def identity_key(el: dict):
    """Identité « métier » d'un élément (sert à détecter les conflits et doublons)."""
    if el["kind"] == "film":
        return ("film", el.get("tmdb") or "", el.get("imdb") or "")
    if el["kind"] == "episode":
        return ("episode", el.get("tvdb") or "", el.get("imdb") or "", el.get("series_guid") or "",
                el.get("s"), el.get("e"))
    return (el["kind"],) + tuple(el["jf_ids"])


# --------------------------------------------------------------------------- identités, fusion, confiance

class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def series_nodes(el: dict):
    out = []
    if el.get("tvdb"):
        out.append("tvdb:" + el["tvdb"])
    if el.get("imdb"):
        out.append("imdb:" + el["imdb"])
    if el.get("series_guid"):
        out.append("sguid:" + el["series_guid"])
    return out


def resolve_series(t: dict, elements: list, stats: collections.Counter):
    """Composantes « série » : deux identifiants sont la même série s'ils apparaissent ensemble sur un même
    élément (même lot UserData ou même fiche Series de la bibliothèque). Un imdb vu avec PLUSIEURS tvdb
    n'est jamais utilisé comme lien (imdb partagé) : un épisode qui n'a que cet imdb est ambigu."""
    imdb_tvdbs = collections.defaultdict(set)
    pairs = []
    for el in elements:
        if el["kind"] == "episode" and el["tvdb"] and el["imdb"]:
            pairs.append((el["tvdb"], el["imdb"]))
    for s in t["items"].values():
        if s["type"] == TYPE_SERIES:
            tv, im, _ = series_ids_of(t["items"], t["providers"], s["id"])
            if tv and im:
                pairs.append((tv, im))
    for tv, im in pairs:
        imdb_tvdbs[im].add(tv)
    shared = {im for im, tvs in imdb_tvdbs.items() if len(tvs) > 1}
    stats["imdb_partage_plusieurs_tvdb"] = len(shared)
    uf = UnionFind()
    for el in elements:
        if el["kind"] != "episode":
            continue
        nodes = [n for n in series_nodes(el) if not (n.startswith("imdb:") and n[5:] in shared)]
        for n in nodes:
            uf.find(n)
        for n in nodes[1:]:
            uf.union(nodes[0], n)
    for tv, im in pairs:
        if im not in shared:
            uf.union("tvdb:" + tv, "imdb:" + im)
    return uf, shared


def finalize(t: dict, elements: list, events: list, stats: collections.Counter):
    """Fusion des doublons, titres, confiance. Renvoie la liste canonique (une entrée par média vu ou non)."""
    uf_series, shared_imdb = resolve_series(t, elements, stats)
    items, providers = t["items"], t["providers"]

    def series_comp(el):
        nodes = [n for n in series_nodes(el) if not (n.startswith("imdb:") and n[5:] in shared_imdb)]
        if not nodes:
            return None
        return uf_series.find(nodes[0])

    # --- alias de séries prouvés par le journal : un même événement désigne l'élément A (ItemId) et
    #     l'élément B (démarrage = LastPlayedDate de B) -> même média (ré-identification de la série).
    #     On ne relie que des épisodes de même S/E, ou deux films.
    uf = UnionFind()
    for i in range(len(elements)):
        uf.find(i)
    by_jf = {}
    for i, el in enumerate(elements):
        for j in el["jf_ids"]:
            by_jf[(el["user_id"], j)] = i
    timeline = collections.defaultdict(lambda: collections.defaultdict(list))
    for i, el in enumerate(elements):
        if el["last_played"]:
            timeline[el["user_id"]][int(to_seconds(el["last_played"]))].append(i)
    series_alias = UnionFind()
    for ev in events:
        if ev["rule"] != "1-itemid" or ev["type"] != "VideoPlayback":
            continue
        a = ev["element"]
        ea = elements[a]
        sec = int(ev["ts"])
        cands = set()
        for d in range(-START_AFTER_S, START_BEFORE_S + 1):
            cands.update(timeline[ev["user_id"]].get(sec + d, []))
        cands.discard(a)
        for b in cands:
            eb = elements[b]
            if ea["kind"] == eb["kind"] == "episode" and (ea["s"], ea["e"]) == (eb["s"], eb["e"]):
                ca, cb = series_comp(ea), series_comp(eb)
                if ca and cb and ca != cb:
                    series_alias.union(ca, cb)
                    stats["preuve_journal_reidentification_episode"] += 1
                uf.union(a, b)
            elif ea["kind"] == eb["kind"] == "film":
                uf.union(a, b)
                stats["preuve_journal_reidentification_film"] += 1
            elif eb["kind"] == "guid_seul" or ea["kind"] == "guid_seul":
                uf.union(a, b)
                stats["preuve_journal_guid_seul"] += 1

    def canon_series(el):
        c = series_comp(el)
        return series_alias.find(c) if c else None

    # --- fusion par identité métier
    ident = {}
    for i, el in enumerate(elements):
        if el["kind"] == "film":
            for k in (("tmdb", el["tmdb"]), ("imdb", el["imdb"])):
                if k[1]:
                    key = (el["user_id"], "film") + k
                    if key in ident:
                        uf.union(i, ident[key])
                    else:
                        ident[key] = i
        elif el["kind"] == "episode":
            cs = canon_series(el)
            if cs:
                key = (el["user_id"], "ep", cs, el["s"], el["e"])
                if key in ident:
                    uf.union(i, ident[key])
                else:
                    ident[key] = i
    # deux éléments portant un même GUID Jellyfin = même média
    for i, el in enumerate(elements):
        for j in el["jf_ids"]:
            uf.union(i, by_jf[(el["user_id"], j)])

    groups = collections.defaultdict(list)
    for i in range(len(elements)):
        groups[uf.find(i)].append(i)
    stats["elements_bruts"] = len(elements)
    stats["doublons_fusionnes"] = len(elements) - len(groups)

    # --- textes du journal par groupe
    texts = collections.defaultdict(collections.Counter)
    ev_count = collections.Counter()
    for ev in events:
        if ev["element"] is not None:
            g = uf.find(ev["element"])
            texts[g][ev["what"]] += 1
            ev_count[g] += 1

    # --- titres de séries : bibliothèque d'abord, sinon préfixe commun des textes du journal
    lib_series_title = collections.defaultdict(set)
    for s in items.values():
        if s["type"] == TYPE_SERIES:
            tv, im, _ = series_ids_of(items, providers, s["id"])
            el = {"tvdb": tv, "imdb": im, "series_guid": "" if (tv or im) else s["id"]}
            c = canon_series(el) if (tv or im) else None
            if c is None and not (tv or im):
                n = "sguid:" + s["id"]
                c = series_alias.find(uf_series.find(n)) if n in uf_series.parent else None
            if c:
                lib_series_title[c].add(s["name"])
    for el in elements:
        if el["kind"] == "episode" and el.get("series_title"):
            c = canon_series(el)
            if c:
                lib_series_title[c].add(el["series_title"])
    log_prefix = collections.defaultdict(list)    # série -> liste d'ensembles de préfixes candidats
    for g, idx in groups.items():
        el = elements[idx[0]]
        if el["kind"] != "episode":
            continue
        c = canon_series(el)
        if not c:
            continue
        for w in texts[g]:
            log_prefix[c].append({p for p, _ in series_prefixes(w)})

    def series_title_of(c):
        lib = lib_series_title.get(c, set())
        if len(lib) == 1:
            return next(iter(lib)), "bibliothèque"
        if len(lib) > 1:
            stats["series_titres_bibliotheque_conflictuels"] += 1
            return "", "conflit de titres"
        cands = log_prefix.get(c)
        if not cands:
            return "", ""
        inter = set.intersection(*cands)
        if len(inter) == 1:
            return next(iter(inter)), "journal"
        if len(inter) > 1:
            # le plus long préfixe commun à tous les textes ; prudence si plusieurs textes ne le départagent pas
            if len(cands) >= 2:
                return max(sorted(inter), key=len), "journal"
            return "", "titre ambigu (un seul texte avec plusieurs ' - ')"
        return "", "textes du journal incohérents"

    entries = []
    for g, idx in sorted(groups.items()):
        els = [elements[i] for i in idx]
        kinds = {e["kind"] for e in els}
        uid = els[0]["user_id"]
        played = max(e["played"] for e in els)
        lpds = sorted(e["last_played"] for e in els if e["last_played"])
        jf = sorted({j for e in els for j in e["jf_ids"]})
        current = sorted({e["current"] for e in els if e["current"]})
        sources = {e["source"] for e in els}
        source = SRC_ITEMS if SRC_ITEMS in sources else SRC_DELETED
        ent = {"user_id": uid, "type": "", "titre_film": "", "titre_serie": "", "titre_episode": "",
               "saison": "", "episode": "", "tmdb": "", "imdb": "", "tvdb": "", "serie_guid": "",
               "tmdb_serie": "", "ids_serie_alias": "",
               "jellyfin_item_id": current[0] if len(current) == 1 else "",
               "jellyfin_ids": " ".join(jf), "date_vue": lpds[0] if lpds else "",
               "date_derniere_lecture": lpds[-1] if lpds else "", "source": source,
               "evenements_journal": ev_count[g], "lignes_fusionnees": len(els),
               "vu": "oui" if played else "non", "confiance": "", "raison": "", "drapeaux": []}
        kinds.discard("guid_seul")
        if len(kinds) > 1:
            ent["type"] = "inconnu"
            ent["confiance"], ent["raison"] = CONF_NO, "type contradictoire (film et épisode réunis)"
        elif not kinds:
            ent["type"] = "inconnu"
            ent["confiance"], ent["raison"] = (
                CONF_NO,
                "GUID Jellyfin seul (élément supprimé sans aucun identifiant fournisseur)",
            )
            if texts[g]:
                ent["titre_film"] = texts[g].most_common(1)[0][0]
                ent["raison"] += " ; texte du journal sans identifiant"
        elif kinds == {"film"}:
            ent["type"] = "film"
            els = [e for e in els if e["kind"] == "film"]
            tm = sorted({e["tmdb"] for e in els if e["tmdb"]})
            im = sorted({e["imdb"] for e in els if e["imdb"]})
            titles = sorted({e["film_title"] for e in els if e.get("film_title")})
            ent["tmdb"], ent["imdb"] = " ".join(tm), " ".join(im)
            if titles:
                ent["titre_film"] = titles[0]
            elif texts[g]:
                ent["titre_film"] = texts[g].most_common(1)[0][0]
                ent["drapeaux"].append("titre issu du journal")
            if len(tm) > 1 or len(im) > 1:
                ent["confiance"], ent["raison"] = CONF_NO, "identifiants de film contradictoires"
            else:
                ent["confiance"] = CONF_SURE
        elif kinds == {"episode"}:
            ent["type"] = "episode"
            els = [e for e in els if e["kind"] == "episode"]
            se = sorted({(e["s"], e["e"]) for e in els})
            tv = sorted({e["tvdb"] for e in els if e["tvdb"]})
            im = sorted({e["imdb"] for e in els if e["imdb"]})
            sg = sorted({e["series_guid"] for e in els if e.get("series_guid")})
            ent["tvdb"], ent["imdb"], ent["serie_guid"] = " ".join(tv), " ".join(im), " ".join(sg)
            ep_titles = sorted({e["episode_title"] for e in els if e.get("episode_title")})
            ent["titre_episode"] = ep_titles[0] if ep_titles else ""
            c = canon_series(els[0])
            ent["_series"] = c
            title, origin = series_title_of(c) if c else ("", "")
            ent["titre_serie"] = title
            if origin == "journal":
                ent["drapeaux"].append("titre de série issu du journal")
            if len(se) > 1:
                ent["confiance"], ent["raison"] = CONF_NO, "numéros saison/épisode contradictoires"
            else:
                ent["saison"], ent["episode"] = se[0]
                if se[0][0] is None or se[0][1] is None:
                    ent["confiance"], ent["raison"] = CONF_NO, "saison ou épisode inconnu"
                elif c is None:
                    ent["confiance"], ent["raison"] = (
                        CONF_NO,
                        "série ambiguë (imdb partagé par plusieurs tvdb, sans tvdb)",
                    )
                elif tv or im:
                    ent["confiance"] = CONF_SURE
                    if len(tv) > 1 or len(im) > 1:
                        ent["drapeaux"].append("série ré-identifiée (preuve journal), plusieurs ids")
                elif title:
                    ent["confiance"], ent["raison"] = (
                        CONF_PROBABLE,
                        "série sans identifiant fournisseur : recherche TMDB par titre",
                    )
                else:
                    ent["confiance"], ent["raison"] = CONF_NO, "série sans identifiant fournisseur ni titre"
        if ent["confiance"] == CONF_SURE and not ent["date_vue"]:
            ent["confiance"], ent["raison"] = CONF_PROBABLE, "date de visionnage inconnue (LastPlayedDate vide)"
        ent["_group"] = g
        ent["_texts"] = sorted(texts[g])
        entries.append(ent)

    # --- identifiants canoniques de chaque série (composante) : tvdb, imdb vus AVEC un tvdb sur un même
    #     élément (sinon tous les imdb), tmdb de la fiche bibliothèque ; les autres ids = alias.
    comp_tv = collections.defaultdict(set)
    comp_im_paired = collections.defaultdict(set)
    comp_im = collections.defaultdict(set)
    comp_sg = collections.defaultdict(set)
    comp_tm = collections.defaultdict(set)
    for el in elements:
        if el["kind"] != "episode":
            continue
        c = canon_series(el)
        if not c:
            continue
        if el["tvdb"]:
            comp_tv[c].add(el["tvdb"])
            if el["imdb"]:
                comp_im_paired[c].add(el["imdb"])
        if el["imdb"]:
            comp_im[c].add(el["imdb"])
        if el.get("series_guid"):
            comp_sg[c].add(el["series_guid"])
        if el.get("tmdb"):
            comp_tm[c].add(el["tmdb"])
    for e in entries:
        c = e.get("_series")
        if e["type"] != "episode" or not c:
            continue
        tv = comp_tv[c]
        im = comp_im_paired[c] or comp_im[c]
        alias = sorted((comp_im[c] - im) | (comp_sg[c] if (tv or im) else set()))
        e["tvdb"], e["imdb"] = " ".join(sorted(tv)), " ".join(sorted(im))
        e["tmdb_serie"] = " ".join(sorted(comp_tm[c]))
        e["serie_guid"] = "" if (tv or im) else " ".join(sorted(comp_sg[c]))
        e["ids_serie_alias"] = " ".join(alias)
        e["drapeaux"] = [d for d in e["drapeaux"] if not d.startswith("série ré-identifiée")]
        if alias:
            e["drapeaux"].append("série ré-identifiée (ids alias conservés)")
        if len(tv) > 1 or len(im) > 1:
            if e["confiance"] == CONF_SURE:
                e["confiance"] = CONF_PROBABLE
                e["raison"] = "plusieurs identifiants pour une même série : à confirmer"
        if e["tvdb"] or e["imdb"]:
            if e["confiance"] != CONF_NO and not e["tmdb_serie"]:
                if "série sans titre : TMDB /find requis" in e["drapeaux"]:
                    e["drapeaux"].remove("série sans titre : TMDB /find requis")
                e["drapeaux"].append("TMDB /find requis" + (" (série sans titre)" if not e["titre_serie"] else ""))
    entries = series_checks(entries, shared_imdb, stats)
    entries = resolve_guid_only(entries, stats)
    entries.extend(journal_only(events, entries, stats))
    return entries, groups, uf


def series_checks(entries: list, shared_imdb: set, stats: collections.Counter):
    """Contrôles de cohérence des séries APRÈS fusion (jamais de rattachement sur le seul titre si des
    identifiants existent) :
      a) imdb de série partagé par plusieurs tvdb -> l'identité de la série est contestée : probable ;
      b) deux séries d'identifiants différents mais de même titre (homonymes) : drapeau ; celle qui n'a
         qu'un imdb (pas de tvdb) passe en probable ;
      c) série SANS identifiant (GUID seul) dont le titre est celui d'UNE seule série identifiée :
         S/E déjà présent -> doublon absorbé ; sinon probable avec les ids de cette série (à confirmer)."""
    comp_ids = collections.defaultdict(lambda: (set(), set()))
    title_comps = collections.defaultdict(set)
    for e in entries:
        c = e.get("_series")
        if e["type"] != "episode" or not c:
            continue
        comp_ids[c][0].update(e["tvdb"].split())
        comp_ids[c][1].update(e["imdb"].split())
        if e["titre_serie"]:
            title_comps[(e["user_id"], norm_title(e["titre_serie"]))].add(c)
    identified = {c for c, (tv, im) in comp_ids.items() if tv or im}
    have = {(e["user_id"], e["_series"], e["saison"], e["episode"]) for e in entries
            if e["type"] == "episode" and e.get("_series")}
    homonym_comps = set()
    for (_uid, _), comps in title_comps.items():
        ids_comps = comps & identified
        if len(ids_comps) > 1:
            homonym_comps |= ids_comps
    stats["series_homonymes_ids_differents"] = len(homonym_comps)
    out = []
    for e in entries:
        c = e.get("_series")
        if e["type"] != "episode" or not c:
            out.append(e)
            continue
        tv, im = comp_ids[c]
        if im & shared_imdb:
            e["drapeaux"].append("imdb de série partagé par plusieurs tvdb")
            if e["confiance"] == CONF_SURE:
                e["confiance"] = CONF_PROBABLE
                e["raison"] = (
                    "imdb de série partagé par plusieurs tvdb : "
                    "identité de série à confirmer (TMDB /find tvdb ET imdb)"
                )
        if c in homonym_comps:
            e["drapeaux"].append("série homonyme d'une autre série (ids différents)")
            if not tv and e["confiance"] == CONF_SURE:
                e["confiance"] = CONF_PROBABLE
                e["raison"] = "série identifiée par imdb seul et homonyme d'une série tvdb : identité à confirmer"
        if c not in identified and e["titre_serie"]:
            targets = title_comps[(e["user_id"], norm_title(e["titre_serie"]))] & identified
            if len(targets) == 1:
                tc = next(iter(targets))
                if (e["user_id"], tc, e["saison"], e["episode"]) in have:
                    stats["doublons_serie_sans_id_par_titre"] += 1
                    continue
                ttv, tim = comp_ids[tc]
                e["tvdb"], e["imdb"] = " ".join(sorted(ttv)), " ".join(sorted(tim))
                e["drapeaux"].append("ids de série repris d'une série identifiée de même titre")
                e["raison"] = "série sans identifiant, titre identique à une seule série identifiée : à confirmer"
                e["confiance"] = CONF_PROBABLE
            elif len(targets) > 1:
                e["confiance"] = CONF_NO
                e["raison"] = "série sans identifiant et titre homonyme de plusieurs séries identifiées"
        out.append(e)
    return out


def series_title_index(entries: list):
    title_series = collections.defaultdict(set)
    have = set()
    for e in entries:
        if e["type"] == "episode" and e.get("_series"):
            if e["titre_serie"]:
                title_series[(e["user_id"], norm_title(e["titre_serie"]))].add(e["_series"])
            have.add((e["user_id"], e["_series"], e["saison"], e["episode"]))
    return title_series, have


def resolve_guid_only(entries: list, stats: collections.Counter):
    """Éléments supprimés connus par leur seul GUID Jellyfin, mais dont une lecture figure au journal :
      - texte de lecture identique à celui d'UN élément identifié -> même média : doublon absorbé ;
      - texte '<Série> - …SxxEyy…' avec série reconnue (titre unique) : S/E présent -> doublon absorbé,
        sinon épisode « probable » (ids de la série, rattachement par titre à confirmer) ;
      - sinon reste non importable."""
    text_owner = collections.defaultdict(set)
    for i, e in enumerate(entries):
        if e["type"] != "inconnu":
            for w in e.get("_texts", []):
                text_owner[(e["user_id"], norm_title(w))].add(i)
    title_series, have = series_title_index(entries)
    sample = {}
    for e in entries:
        if e.get("_series"):
            sample.setdefault(e["_series"], e)
    out = []
    for e in entries:
        if e["type"] != "inconnu" or e["source"] == SRC_LOG or not e.get("_texts"):
            out.append(e)
            continue
        owners = set()
        for w in e["_texts"]:
            owners |= text_owner.get((e["user_id"], norm_title(w)), set())
        if len(owners) == 1:
            stats["doublons_guid_seul_meme_texte"] += 1
            continue
        w = e["_texts"][0]
        se = sxxeyy(w)
        series, title = set(), ""
        for p, _ in series_prefixes(w):
            cand = title_series.get((e["user_id"], norm_title(p)))
            if cand:
                series |= cand
                title = p
        if se and len(series) == 1:
            c = next(iter(series))
            if (e["user_id"], c, se[0], se[1]) in have:
                stats["doublons_guid_seul_titre_se"] += 1
                continue
            ref = sample[c]
            e.update({"type": "episode", "titre_serie": title, "titre_film": "", "saison": se[0], "episode": se[1],
                      "tvdb": ref["tvdb"], "imdb": ref["imdb"], "serie_guid": ref["serie_guid"],
                      "tmdb_serie": ref["tmdb_serie"], "_series": c})
            if (ref["tvdb"] or ref["imdb"]) and e["vu"] == "oui":
                e["confiance"] = CONF_PROBABLE
                e["raison"] = "GUID seul ; série et S/E lus dans le texte du journal : à confirmer"
        out.append(e)
    return out


def journal_only(events: list, entries: list, stats: collections.Counter):
    """Lectures du journal qu'aucune règle n'a rattachées à un élément UserData.
    Regroupées par texte ; on tente '<Série> - <…SxxEyy…>' :
      - série reconnue (titre identique, à la casse/ponctuation près, d'UNE seule série connue) et S/E
        déjà présent -> doublon absorbé ;
      - série reconnue et S/E absent -> entrée « journal », probable (lecture attestée, fin non prouvée) ;
      - sinon -> entrée « journal », non importable (titre sans identifiant)."""
    title_series, have = series_title_index(entries)
    # identifiants d'une série (pour remplir l'entrée journal)
    series_ids = collections.defaultdict(lambda: (set(), set(), set()))
    for e in entries:
        if e["type"] == "episode" and e.get("_series"):
            tv, im, sg = series_ids[e["_series"]]
            tv.update(x for x in e["tvdb"].split() if x)
            im.update(x for x in e["imdb"].split() if x)
            sg.update(x for x in e["serie_guid"].split() if x)
    known_text = collections.defaultdict(set)
    for i, e in enumerate(entries):
        if e["type"] == "inconnu":
            continue
        names = list(e.get("_texts", []))
        if e["titre_film"]:
            names.append(e["titre_film"])
        if e["titre_serie"] and e["titre_episode"]:
            names.append(e["titre_serie"] + " - " + e["titre_episode"])
        for w in names:
            known_text[(e["user_id"], norm_title(w))].add(i)
    by_text = collections.defaultdict(list)
    for ev in events:
        if ev["element"] is None:
            by_text[ev["key"]].append(ev)
    out = {}
    for (uid, _), evs in sorted(by_text.items()):
        evs.sort(key=lambda x: x["date"])
        what = evs[0]["what"]
        owners = known_text.get((uid, norm_title(what)), set())
        if len(owners) == 1:
            stats["journal_seul_doublon_absorbe_textes"] += 1
            stats["journal_seul_doublon_absorbe_evenements"] += len(evs)
            continue
        se = sxxeyy(what)
        series = set()
        title = ""
        for p, _rest in series_prefixes(what):
            cand = title_series.get((uid, norm_title(p)))
            if cand:
                series |= cand
                title = p
        ent = {"user_id": uid, "type": "inconnu", "titre_film": "", "titre_serie": "", "titre_episode": "",
               "saison": "", "episode": "", "tmdb": "", "imdb": "", "tvdb": "", "serie_guid": "",
               "tmdb_serie": "", "ids_serie_alias": "",
               "jellyfin_item_id": "", "jellyfin_ids": "", "date_vue": evs[0]["date"],
               "date_derniere_lecture": evs[-1]["date"], "source": SRC_LOG,
               "evenements_journal": len(evs), "lignes_fusionnees": 0, "vu": "lecture seule",
               "confiance": CONF_NO, "raison": "", "drapeaux": [], "_texts": [what]}
        if se and len(series) == 1:
            c = next(iter(series))
            if (uid, c, se[0], se[1]) in have:
                stats["journal_seul_doublon_absorbe_textes"] += 1
                stats["journal_seul_doublon_absorbe_evenements"] += len(evs)
                continue
            key = (uid, c, se[0], se[1])
            if key in out:                      # même épisode sous deux textes (deux fichiers)
                o = out[key]
                o["evenements_journal"] += len(evs)
                o["date_vue"] = min(o["date_vue"], evs[0]["date"])
                o["date_derniere_lecture"] = max(o["date_derniere_lecture"], evs[-1]["date"])
                o["_texts"].append(what)
                stats["journal_seul_doublon_interne"] += 1
                continue
            tv, im, sg = series_ids[c]
            ent.update({"type": "episode", "titre_serie": title, "saison": se[0], "episode": se[1],
                        "tvdb": " ".join(sorted(tv)), "imdb": " ".join(sorted(im)),
                        "serie_guid": " ".join(sorted(sg)), "_series": c})
            if tv or im:
                ent["confiance"] = CONF_PROBABLE
                ent["raison"] = "journal seul : lecture attestée, visionnage complet non prouvé"
            else:
                ent["confiance"] = CONF_NO
                ent["raison"] = "journal seul et série sans identifiant fournisseur"
            out[key] = ent
            continue
        if se and len(series) > 1:
            ent["raison"] = "journal seul : titre de série homonyme de plusieurs séries"
        elif se:
            ent["type"] = "episode"
            ent["raison"] = "journal seul : série inconnue (titre sans identifiant)"
        elif series:
            ent["type"] = "episode"
            ent["raison"] = "journal seul : épisode sans numéro saison/épisode"
        else:
            ent["raison"] = "journal seul : titre sans identifiant"
        title_key = "titre_film" if ent["type"] == "inconnu" else "titre_serie"
        ent[title_key] = what if ent["type"] == "inconnu" else (title or what)
        out[("txt", uid, norm_title(what))] = ent
    return list(out.values())


# --------------------------------------------------------------------------- sorties

CSV_COLUMNS = ["utilisateur", "type", "titre_film", "titre_serie", "saison", "episode", "titre_episode",
               "tmdb", "imdb", "tvdb", "tmdb_serie", "serie_guid", "ids_serie_alias", "jellyfin_item_id",
               "jellyfin_ids", "date_vue", "date_derniere_lecture", "vu", "source", "confiance", "raison",
               "drapeaux", "evenements_journal", "lignes_fusionnees"]


def run(path: str):
    t = load(path)
    stats = collections.Counter()
    elements, unassigned = build(t, stats)
    stats["guid_detaches_non_attribues"] = len(unassigned)
    events = associate_logs(t, elements, stats)
    entries, _, _ = finalize(t, elements, events, stats)
    users = t["users"]
    rows = []
    for e in entries:
        r = {k: e.get(k, "") for k in CSV_COLUMNS}
        r["utilisateur"] = users.get(e["user_id"], e["user_id"])
        r["drapeaux"] = " | ".join(e["drapeaux"])
        for k in ("saison", "episode"):
            r[k] = "" if r[k] in (None, "") else str(r[k])
        r["evenements_journal"] = str(r["evenements_journal"])
        r["lignes_fusionnees"] = str(r["lignes_fusionnees"])
        rows.append(r)
    rows.sort(key=lambda r: (r["utilisateur"], r["type"], norm_title(r["titre_serie"] or r["titre_film"]),
                             r["tvdb"], r["imdb"], r["tmdb"], r["serie_guid"],
                             int(r["saison"] or -1), int(r["episode"] or -1), r["date_vue"], r["jellyfin_ids"],
                             norm_title(r["titre_film"])))
    return t, rows, stats, events


def kept(rows):
    """Liste canonique : éléments vus (UserData Played) + lectures du journal seul. Les non-vus sont exclus."""
    return [r for r in rows if r["vu"] in ("oui", "lecture seule")]


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def compute_stats(t, rows, stats, events):
    """Chiffres agrégés SANS aucune donnée nominative (pas de titres, utilisateurs, ids)."""
    k = kept(rows)
    out = {"lignes_userdata": stats["userdata_lignes"], "utilisateurs": len(t["users"]),
           "evenements_journal_lecture": len(events)}
    c = collections.Counter()
    for r in k:
        c[(r["type"], r["confiance"])] += 1
    out["par_type_confiance"] = {f"{a} / {b}": n for (a, b), n in sorted(c.items())}
    out["sur_films"] = c[("film", CONF_SURE)]
    out["sur_episodes"] = c[("episode", CONF_SURE)]
    out["probables"] = sum(n for (a, b), n in c.items() if b == CONF_PROBABLE)
    out["non_importables"] = sum(n for (a, b), n in c.items() if b == CONF_NO)
    prob_raisons = collections.Counter(r["raison"] for r in k if r["confiance"] == CONF_PROBABLE)
    out["raisons_probable"] = dict(sorted(prob_raisons.items()))
    no_raisons = collections.Counter(r["raison"] for r in k if r["confiance"] == CONF_NO)
    out["raisons_non_importable"] = dict(sorted(no_raisons.items()))
    out["exclus_non_vus"] = dict(sorted(collections.Counter(r["type"] for r in rows if r["vu"] == "non").items()))
    out["par_source"] = dict(sorted(collections.Counter((r["source"]) for r in k).items()))
    imp = [r for r in k if r["confiance"] in (CONF_SURE, CONF_PROBABLE)]
    out["supprimes_recuperes_importables"] = sum(1 for r in imp if r["source"] == SRC_DELETED)
    # séries
    def skey(r):
        return (r["utilisateur"], r["tvdb"], r["imdb"], r["serie_guid"])
    ep_imp = [r for r in imp if r["type"] == "episode"]
    series = {skey(r) for r in ep_imp}
    out["series_distinctes_importables"] = len(series)
    out["series_par_tvdb"] = len({s for s in series if s[1]})
    out["series_par_imdb_seul"] = len({s for s in series if s[2] and not s[1]})
    out["series_par_titre_seul"] = len({s for s in series if not (s[1] or s[2])})
    no_title = {skey(r) for r in ep_imp if not r["titre_serie"] and (r["tvdb"] or r["imdb"])}
    out["series_ids_sans_titre_find_requis"] = len(no_title)
    out["series_find_requis_total"] = len({skey(r) for r in ep_imp if (r["tvdb"] or r["imdb"]) and not r["tmdb_serie"]})
    out["episodes_find_requis_sans_titre"] = sum(1 for r in ep_imp if not r["titre_serie"])
    out["series_tmdb_connu_bibliotheque"] = len({skey(r) for r in ep_imp if r["tmdb_serie"]})
    out["films_titre_inconnu_sur"] = sum(1 for r in imp if r["type"] == "film" and not r["titre_film"])
    out["series_homonymes_ids_differents"] = stats["series_homonymes_ids_differents"]
    out["imdb_serie_partage_plusieurs_tvdb"] = stats["imdb_partage_plusieurs_tvdb"]
    out["series_reidentifiees_ids_alias"] = len({skey(r) for r in ep_imp if r["ids_serie_alias"]})
    # doublons
    out["doublons"] = {
        "elements_userdata_bruts": stats["elements_bruts"],
        "fusionnes_meme_identite_ou_preuve_journal": stats["doublons_fusionnes"],
        "dont_preuve_journal_reidentification": stats["preuve_journal_reidentification_episode"]
        + stats["preuve_journal_reidentification_film"],
        "serie_sans_id_absorbes_par_titre": stats["doublons_serie_sans_id_par_titre"],
        "guid_seul_absorbes": stats["doublons_guid_seul_meme_texte"] + stats["doublons_guid_seul_titre_se"],
        "journal_seul_textes_absorbes": stats["journal_seul_doublon_absorbe_textes"],
    }
    out["guid_detaches_non_attribues"] = stats["guid_detaches_non_attribues"]
    # journal
    out["journal"] = {
        k2[len("journal_regle_"):]: v
        for k2, v in sorted(stats.items())
        if k2.startswith("journal_regle_")
    }
    out["journal"]["demarrage_ambigu"] = stats["journal_demarrage_ambigu"]
    out["journal"]["non_parse"] = stats["journal_non_parse"]
    # invariants
    seen = collections.Counter()
    for r in imp:
        if r["type"] == "film":
            seen[("f", r["utilisateur"], r["tmdb"] or r["imdb"])] += 1
        else:
            seen[("e",) + skey(r) + (r["saison"], r["episode"])] += 1
    film_ids = collections.Counter()
    for r in imp:
        if r["type"] == "film":
            for x in (r["tmdb"], r["imdb"]):
                if x:
                    film_ids[(r["utilisateur"], x)] += 1
    out["invariants"] = {
        "doublons_restants": sum(n - 1 for n in seen.values() if n > 1),
        "doublons_ids_films": sum(n - 1 for n in film_ids.values() if n > 1),
        "episodes_importables_sans_serie": sum(1 for r in ep_imp if not (r["tvdb"] or r["imdb"] or r["titre_serie"])),
        "series_inconnu": sum(1 for r in ep_imp if norm_title(r["titre_serie"]) in ("inconnu", "unknown")),
        "episodes_importables_sans_saison_episode": sum(1 for r in ep_imp if r["saison"] == "" or r["episode"] == ""),
        "importables_sans_date": sum(1 for r in imp if not r["date_vue"]),
        "jellyfin_item_id_mal_forme": sum(
            1 for r in k
            if r["jellyfin_item_id"] and not re.fullmatch(r"[0-9a-f]{32}", r["jellyfin_item_id"])
        ),
        "jellyfin_item_id_placeholder": sum(1 for r in k if PLACEHOLDER_ID in r["jellyfin_ids"].split()),
        "sur_avec_plusieurs_tvdb_ou_imdb": sum(
            1 for r in k
            if r["confiance"] == CONF_SURE
            and (
                len(r["tvdb"].split()) > 1
                or len(r["imdb"].split()) > 1
                or len(r["tmdb"].split()) > 1
            )
        ),
        "sur_issus_du_journal_seul": sum(1 for r in k if r["confiance"] == CONF_SURE and r["source"] == SRC_LOG),
    }
    # répartition par année (date de visionnage)
    years = collections.defaultdict(collections.Counter)
    for r in k:
        y = r["date_vue"][:4] or "inconnue"
        years[y][r["confiance"]] += 1
    out["par_annee"] = {y: dict(sorted(v.items())) for y, v in sorted(years.items())}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", help="jellyfin.db (SQLite) ou sauvegarde .zip 10.11")
    ap.add_argument("--csv", help="écrit la liste canonique (éléments vus + journal seul)")
    ap.add_argument("--csv-tout", help="écrit aussi les éléments non vus (contrôle)")
    ap.add_argument("--stats", help="écrit les chiffres agrégés (JSON, sans donnée nominative)")
    ap.add_argument("--compare", metavar="SOURCE_B", help="compare la sortie avec une seconde source")
    a = ap.parse_args(argv)
    t, rows, stats, events = run(a.source)
    k = kept(rows)
    if a.csv:
        write_csv(k, a.csv)
    if a.csv_tout:
        write_csv(rows, a.csv_tout)
    st = compute_stats(t, rows, stats, events)
    if a.stats:
        with open(a.stats, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    dup = st["doublons"]["fusionnes_meme_identite_ou_preuve_journal"]
    print(
        f"sûrs : films {st['sur_films']}, épisodes {st['sur_episodes']} | probables {st['probables']} | "
        f"non importables {st['non_importables']} | doublons fusionnés {dup}"
        f" | séries /find requis {st['series_find_requis_total']}"
    )
    if a.compare:
        t2, rows2, stats2, events2 = run(a.compare)
        st2 = compute_stats(t2, rows2, stats2, events2)
        a_set = collections.Counter(tuple(r[c] for c in CSV_COLUMNS) for r in rows)
        b_set = collections.Counter(tuple(r[c] for c in CSV_COLUMNS) for r in rows2)
        only_a, only_b = a_set - b_set, b_set - a_set
        same_stats = st == st2
        only_a_n, only_b_n = sum(only_a.values()), sum(only_b.values())
        idem = "oui" if same_stats else "non"
        print(
            f"comparaison : {only_a_n} ligne(s) seulement dans A, {only_b_n} seulement dans B, "
            f"statistiques identiques : {idem}"
        )
        if only_a or only_b or not same_stats:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
