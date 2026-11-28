from fastapi import APIRouter

from mediatracker.api.v1 import (
    alldebrid,
    auth,
    catalog,
    cleanup,
    dashboard,
    downloads,
    events,
    ha,
    health,
    hive,
    images,
    jellyfin,
    jellyfin_history,
    library,
    links,
    maintenance,
    media,
    metadata,
    metro,
    providers,
    quality_profiles,
    settings,
    setup,
    source_bindings,
    storage,
    task_runs,
    tmdb_trial,
    tokens,
    wawacity_domain,
    webhooks,
)

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(setup.router)
router.include_router(tokens.router)
router.include_router(providers.router)
router.include_router(settings.router)
router.include_router(wawacity_domain.router)
router.include_router(quality_profiles.router)
router.include_router(storage.router)
router.include_router(health.router)
router.include_router(task_runs.router)
router.include_router(metro.router)
router.include_router(images.router)
router.include_router(ha.router)
router.include_router(media.router)
router.include_router(source_bindings.router, prefix="/media")
router.include_router(catalog.router)
router.include_router(media.item_router)
router.include_router(links.router)
router.include_router(library.router)
router.include_router(jellyfin_history.router)
router.include_router(maintenance.router)
router.include_router(downloads.router)
router.include_router(metadata.router)
router.include_router(dashboard.router)
router.include_router(tmdb_trial.router)
router.include_router(jellyfin.router)
router.include_router(alldebrid.router)
router.include_router(events.router)
router.include_router(hive.router)
router.include_router(webhooks.router)
router.include_router(cleanup.router)
router.include_router(cleanup.trash_router)
