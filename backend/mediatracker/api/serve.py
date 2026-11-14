"""API process entry. Logging is configured before Uvicorn touches it.

``uvicorn.run`` turns ``log_config=None`` into the default LOGGING_CONFIG (text
handlers on uvicorn loggers). This module passes ``UVICORN_LOG_CONFIG_SKIP`` so
Uvicorn does not stack handlers before the app import.
"""

import os
from pathlib import Path

import uvicorn

from mediatracker.config import get_settings
from mediatracker.security.logging import UVICORN_LOG_CONFIG_SKIP, configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(
        settings.log_level,
        Path(settings.config_dir) / "logs",
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )
    uvicorn.run(
        "mediatracker.api.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        log_config=UVICORN_LOG_CONFIG_SKIP,
        access_log=True,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
    )


if __name__ == "__main__":
    main()
