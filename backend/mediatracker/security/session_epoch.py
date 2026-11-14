"""Invalidation des sessions navigateur signées via un compteur par utilisateur."""

from sqlalchemy.orm import Session

from mediatracker.db.models import User


def bump_session_epoch(db: Session, user: User) -> int:
    """Incrémente le compteur et invalide toutes les sessions signées avec un epoch antérieur."""
    user.session_epoch += 1
    db.commit()
    db.refresh(user)
    return user.session_epoch
