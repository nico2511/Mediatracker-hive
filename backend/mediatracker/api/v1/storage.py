"""Resolved library, download and trash directories, plus the same-disk check."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.core.storage import same_filesystem_warning, storage_layout
from mediatracker.maintenance.restore_catalog import backups_root

router = APIRouter(prefix="/storage", tags=["storage"])


class StorageOut(BaseModel):
    media_dir: str
    movies_dir: str
    series_dir: str
    downloads_dir: str
    trash_dir: str
    backups_dir: str
    same_filesystem: bool
    warning: str | None


@router.get("", response_model=StorageOut)
def get_storage(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> StorageOut:
    del _principal
    settings = request.app.state.settings
    layout = storage_layout(db, settings)
    warning = same_filesystem_warning(layout)
    return StorageOut(
        media_dir=str(layout.media_dir),
        movies_dir=str(layout.movies_dir),
        series_dir=str(layout.series_dir),
        downloads_dir=str(layout.downloads_dir),
        trash_dir=str(layout.trash_dir),
        backups_dir=str(backups_root(settings)),
        same_filesystem=not warning,
        warning=warning or None,
    )
