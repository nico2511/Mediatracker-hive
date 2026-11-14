# Instructions pour agents (MediaTracker)

## Arborescence

- **Racine du dépôt** : application actuelle (FastAPI `backend/`, UI Vue `frontend/`, Docker, tests `tests/`).
- Documentation utilisateur : `docs/` (hors `docs/interne/` et `docs/archives/` pour le public).
- Référence développeur : `docs/interne/REFERENCE-DEV.md`.

## Commandes utiles

```bash
pip install -e ".[dev]"
ruff check backend tests
pytest -q
docker build -t mediatracker:local .
python scripts/build_ai_doc.py   # régénère docs/llms-full.md et docs/llms.txt
```

Frontend : `cd frontend && npm ci && npm run dev`.

## CI

L’image Docker publique est **`ghcr.io/nico2511/mediatracker-hive:latest`**.

## Contraintes

- Ne pas ajouter de secrets, domaines personnels, IP privées ni titres réels de films/séries dans le dépôt.
- Ne pas publier d’image ni déclencher la CI de publication depuis un agent sans instruction explicite.
