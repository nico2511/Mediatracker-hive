"""Graphe statique lignes · stations · correspondances (plan carte réseau)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EdgeKind = Literal["declenche", "alimente", "attend", "bloque"]

LINE_COLORS: dict[str, str] = {
    "Sources": "#FFCD00",
    "Suivi": "#007852",
    "AllDebrid": "#FF7E2E",
    "Telechargements": "#003CA6",
    "Rangement": "#CF009E",
    "Bibliotheque": "#837902",
    "Jellyfin": "#62259D",
    "TMDB": "#6EC4E8",
    "Ruche": "#C9910D",
    "Corbeille": "#704B1C",
    "Notifications": "#FA9ABA",
    "Socle": "#8D8D8D",
}

# station_id -> (line label, human label)
STATIONS: dict[str, tuple[str, str]] = {
    "SRC1": ("Sources", "Sondage dû"),
    "SRC2": ("Sources", "Vague forcée"),
    "SRC3": ("Sources", "Orchestration profil×source"),
    "SRC4": ("Sources", "Liens hébergeurs"),
    "SRC5": ("Sources", "Comet / Torrentio / Stremio"),
    "SRC6": ("Sources", "Hive comme source"),
    "SRC7": ("Sources", "Repli épisodes"),
    "SRC8": ("Sources", "Application des liens"),
    "SRC9": ("Sources", "Observations sources"),
    "SRC10": ("Sources", "Revérification liens morts"),
    "SUI1": ("Suivi", "Suivre"),
    "SUI2": ("Suivi", "Ne plus suivre"),
    "SUI3": ("Suivi", "Arrêt auto du suivi"),
    "SUI4": ("Suivi", "Retrait films obtenus"),
    "SUI5": ("Suivi", "Portail de sondage"),
    "SUI6": ("Suivi", "Proposition remplacement"),
    "SUI7": ("Suivi", "À trier"),
    "ADB1": ("AllDebrid", "Santé / AUTH_BLOCKED"),
    "ADB2": ("AllDebrid", "Cache aimants"),
    "ADB3": ("AllDebrid", "Débridage"),
    "ADB4": ("AllDebrid", "Purge du compte"),
    "DL1": ("Telechargements", "File / démarrage"),
    "DL2": ("Telechargements", "Envoi aria2"),
    "DL3": ("Telechargements", "Démon aria2"),
    "DL4": ("Telechargements", "Websocket aria2"),
    "DL5": ("Telechargements", "Suivi actif"),
    "DL6": ("Telechargements", "Bloqués / bascule"),
    "DL7": ("Telechargements", "Réconciliation aria2"),
    "DL8": ("Telechargements", "Garde disque"),
    "DL9": ("Telechargements", "Pause"),
    "DL10": ("Telechargements", "Nettoyage incomplets"),
    "RNG1": ("Rangement", "Import prêt"),
    "RNG2": ("Rangement", "Vérification fichier Hive"),
    "RNG3": ("Rangement", "Contrôle intégrité"),
    "RNG4": ("Rangement", "Garder le meilleur"),
    "RNG5": ("Rangement", "Finalisation"),
    "RNG6": ("Rangement", "Épisodes frères obtenus"),
    "BIB1": ("Bibliotheque", "Surveillance inotify"),
    "BIB2": ("Bibliotheque", "Scan bibliothèque"),
    "BIB3": ("Bibliotheque", "Garde volume"),
    "BIB4": ("Bibliotheque", "Présence fichier"),
    "BIB5": ("Bibliotheque", "Rattachement différé"),
    "BIB6": ("Bibliotheque", "Vérification globale"),
    "JF1": ("Jellyfin", "Refresh bibliothèque"),
    "JF2": ("Jellyfin", "Synchro vu"),
    "JF3": ("Jellyfin", "Webhook"),
    "JF4": ("Jellyfin", "Alignement"),
    "JF5": ("Jellyfin", "Push identité"),
    "JF6": ("Jellyfin", "Import historique"),
    "TMDB1": ("TMDB", "Catalogue nouveautés"),
    "TMDB2": ("TMDB", "Sorties numériques"),
    "TMDB3": ("TMDB", "Saisons / films"),
    "TMDB4": ("TMDB", "Purge cache images"),
    "RUC1": ("Ruche", "Empreintes"),
    "RUC2": ("Ruche", "Catalogue"),
    "RUC3": ("Ruche", "Gossip"),
    "RUC4": ("Ruche", "Rafale gossip"),
    "RUC5": ("Ruche", "Signal cherche"),
    "RUC6": ("Ruche", "Auto-prise Recherché"),
    "RUC7": ("Ruche", "Auto-acceptation"),
    "RUC8": ("Ruche", "Offres → file"),
    "RUC9": ("Ruche", "Demande de grant"),
    "RUC10": ("Ruche", "Émission de grant"),
    "RUC11": ("Ruche", "Service fichier"),
    "RUC12": ("Ruche", "Renouvellement grants"),
    "RUC13": ("Ruche", "Gossip entrant"),
    "CRB1": ("Corbeille", "Demande classement"),
    "CRB2": ("Corbeille", "Classement / préavis"),
    "CRB3": ("Corbeille", "Mise en corbeille"),
    "CRB4": ("Corbeille", "Purge corbeille"),
    "CRB5": ("Corbeille", "Restauration"),
    "CRB6": ("Corbeille", "Fichier remplacé"),
    "NOT1": ("Notifications", "Journal"),
    "NOT2": ("Notifications", "Livraison"),
    "NOT3": ("Notifications", "Canaux"),
    "NOT4": ("Notifications", "Lecture HA"),
    "NOT5": ("Notifications", "Actions HA"),
    "NOT6": ("Notifications", "Purge journal"),
    "SOC1": ("Socle", "Passe worker"),
    "SOC2": ("Socle", "Sommeil et réveil"),
    "SOC3": ("Socle", "Porte maintenance"),
    "SOC4": ("Socle", "Sauvegarde SQLite"),
    "SOC5": ("Socle", "Maintenance SQLite"),
    "SOC6": ("Socle", "Métriques"),
    "SOC7": ("Socle", "Base occupée"),
}

# Stations effectivement instrumentées dans cette PR (source : metro/instrument.py).
INSTRUMENTED_PR1: frozenset[str] = frozenset(
    {
        "SRC1",
        "SRC8",
        "ADB2",
        "ADB3",
        "DL1",
        "RNG1",
        "RNG5",
        "RUC2",
        "RUC3",
        "JF1",
        "JF2",
        "JF4",
        "SUI3",
        "SUI4",
        "SUI6",
        "SOC1",
    }
)

NOT_INSTRUMENTED_PR1: frozenset[str] = frozenset(set(STATIONS) - INSTRUMENTED_PR1)

# Correspondances non traçables sans registre des causes de réveil (décision mainteneur).
UNTRACEABLE_EDGES: frozenset[str] = frozenset({"C02", "C03", "C09", "C15", "C20", "C26", "C36", "C49", "C56"})


@dataclass(frozen=True, slots=True)
class MetroEdge:
    edge_id: str
    origin: str
    dest: str
    kind: EdgeKind


def _edges() -> tuple[MetroEdge, ...]:
    raw: list[tuple[str, str, str, EdgeKind]] = [
        ("C01", "SRC8", "DL1", "alimente"),
        ("C02", "SRC8", "DL1", "declenche"),
        ("C03", "DL1", "SRC1", "declenche"),
        ("C04", "DL1", "ADB2", "attend"),
        ("C05", "DL1", "ADB3", "attend"),
        ("C06", "ADB1", "DL1", "bloque"),
        ("C07", "DL1", "SRC5", "attend"),
        ("C08", "DL2", "DL3", "declenche"),
        ("C09", "DL4", "SOC2", "declenche"),
        ("C10", "DL5", "RNG1", "declenche"),
        ("C11", "DL8", "DL1", "bloque"),
        ("C12", "DL9", "DL1", "bloque"),
        ("C13", "RNG5", "SUI3", "declenche"),
        ("C14", "RNG5", "JF1", "alimente"),
        ("C15", "RNG5", "CRB1", "declenche"),
        ("C16", "RNG5", "RUC2", "declenche"),
        ("C17", "RNG5", "NOT1", "alimente"),
        ("C18", "RNG5", "SUI6", "declenche"),
        ("C19", "RNG5", "BIB2", "alimente"),
        ("C20", "RNG1", "BIB1", "declenche"),
        ("C21", "RNG4", "CRB6", "declenche"),
        ("C22", "RNG2", "RUC1", "alimente"),
        ("C23", "SUI4", "SRC1", "bloque"),
        ("C24", "SUI5", "SRC1", "attend"),
        ("C25", "SRC3", "SUI6", "declenche"),
        ("C26", "SUI6", "DL1", "declenche"),
        ("C27", "SUI1", "SRC1", "alimente"),
        ("C28", "SUI1", "BIB5", "declenche"),
        ("C29", "SUI2", "SUI7", "alimente"),
        ("C30", "SRC10", "DL1", "alimente"),
        ("C31", "NOT5", "SRC2", "declenche"),
        ("C32", "SRC3", "RUC5", "declenche"),
        ("C33", "SUI1", "RUC5", "declenche"),
        ("C34", "SUI2", "RUC5", "declenche"),
        ("C35", "SRC3", "SRC6", "attend"),
        ("C36", "RUC8", "DL1", "declenche"),
        ("C37", "RUC6", "SUI1", "declenche"),
        ("C38", "RUC6", "SRC3", "alimente"),
        ("C39", "RUC7", "RUC2", "declenche"),
        ("C40", "RUC2", "RUC1", "declenche"),
        ("C41", "RUC1", "RUC2", "attend"),
        ("C42", "RUC1", "RUC2", "declenche"),
        ("C43", "RUC2", "RUC4", "declenche"),
        ("C44", "RUC2", "RUC5", "declenche"),
        ("C45", "DL1", "RUC9", "attend"),
        ("C46", "RUC11", "DL1", "bloque"),
        ("C47", "RUC11", "RUC2", "attend"),
        ("C48", "DL7", "RUC12", "declenche"),
        ("C49", "BIB1", "BIB2", "declenche"),
        ("C50", "BIB2", "DL1", "bloque"),
        ("C51", "BIB2", "RUC2", "declenche"),
        ("C52", "BIB3", "SRC1", "bloque"),
        ("C53", "BIB4", "SUI3", "declenche"),
        ("C54", "JF2", "CRB1", "declenche"),
        ("C55", "JF2", "SUI3", "declenche"),
        ("C56", "JF3", "SOC2", "declenche"),
        ("C57", "JF1", "JF4", "attend"),
        ("C58", "JF4", "JF5", "declenche"),
        ("C59", "JF4", "CRB1", "declenche"),
        ("C60", "CRB2", "DL1", "attend"),
        ("C61", "CRB3", "DL1", "declenche"),
        ("C62", "CRB2", "NOT1", "alimente"),
        ("C63", "TMDB3", "SRC1", "alimente"),
        ("C64", "TMDB3", "SUI3", "declenche"),
        ("C65", "TMDB3", "NOT1", "alimente"),
        ("C66", "TMDB3", "SUI5", "alimente"),
        ("C67", "TMDB2", "NOT4", "alimente"),
        ("C68", "SRC9", "NOT1", "alimente"),
        ("C69", "NOT1", "NOT2", "declenche"),
        ("C70", "SOC3", "SOC1", "bloque"),
        ("C71", "SOC7", "SOC1", "bloque"),
        ("C72", "RUC1", "SOC1", "attend"),
    ]
    return tuple(MetroEdge(eid, o, d, k) for eid, o, d, k in raw)


EDGES: tuple[MetroEdge, ...] = _edges()
EDGE_BY_ID: dict[str, MetroEdge] = {edge.edge_id: edge for edge in EDGES}


def station_line(station_id: str) -> str:
    meta = STATIONS.get(station_id)
    if meta is None:
        return "Socle"
    return meta[0]


def edge_between(origin: str, dest: str, kind: EdgeKind | None = None) -> str | None:
    for edge in EDGES:
        if edge.origin == origin and edge.dest == dest and (kind is None or edge.kind == kind):
            return edge.edge_id
    return None
