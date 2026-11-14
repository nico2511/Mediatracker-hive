"""Download queue. Importing this package does not open a network client."""

from mediatracker.core.pipeline.clients import _debrid, _downloader, close_cached_clients
from mediatracker.core.pipeline.constants import BACKOFFS, french_error
from mediatracker.core.pipeline.queue import (
    cancel_job,
    queue_pause,
    retry_job,
    schedule_full_retries,
    tick,
)
from mediatracker.core.pipeline.siblings import _settle_obtained_siblings
from mediatracker.core.pipeline.unlock import _direct_reject_reason, _magnet_keys, _redirector_targets

__all__ = [
    "BACKOFFS",
    "_debrid",
    "_direct_reject_reason",
    "_downloader",
    "_magnet_keys",
    "_redirector_targets",
    "_settle_obtained_siblings",
    "cancel_job",
    "close_cached_clients",
    "french_error",
    "queue_pause",
    "retry_job",
    "schedule_full_retries",
    "tick",
]
