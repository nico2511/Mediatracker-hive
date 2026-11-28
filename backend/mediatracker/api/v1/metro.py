"""Carte réseau : graphe statique et synthèse d'activité."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.metro.summary import activity_summary
from mediatracker.metro.ui_graph import graph_payload

router = APIRouter(prefix="/metro", tags=["metro"])


@router.get("/graph")
def get_metro_graph(_user=Depends(require_scope("read"))) -> dict:
    return graph_payload()


@router.get("/activity")
def get_metro_activity(
    db: Session = Depends(get_db),
    _user=Depends(require_scope("read")),
    hours: float = Query(24.0, ge=1.0, le=168.0),
) -> dict:
    body = activity_summary(db, hours=hours)
    graph = graph_payload()
    graph["diagnostics"] = body.get("diagnostics") or []
    body["graph"] = graph
    return body