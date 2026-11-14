"""Stop the container so Docker's unless-stopped policy starts it again.

The API finishes the current database commit and asks aria2 to pause and save
its session. It then exits 1. Under s6 that exit is the container's: the halt
program reads the code we write. Outside the image, the process itself exits.
"""

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_HALT = Path("/run/s6/basedir/bin/halt")
_EXIT_CODE = Path("/run/s6-linux-init-container-results/exitcode")
_DELAY_SECONDS = 0.4


def pause_downloader(downloader) -> None:
    """Pause every aria2 download and flush its session. A miss does not block the restart."""
    if downloader is None or not hasattr(downloader, "pause_all"):
        return
    try:
        downloader.pause_all()
    except Exception:
        logger.info("Pause aria2 ignorée avant redémarrage.")


def container_stop(
    code: int = 1,
    *,
    halt: Path | None = None,
    exitcode_path: Path | None = None,
    exit_fn=os._exit,
) -> None:
    """Leave a non-zero status for the restart policy, then stop."""
    program = halt if halt is not None else _HALT
    destination = exitcode_path if exitcode_path is not None else _EXIT_CODE
    if program.is_file():
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(f"{code}\n", encoding="ascii")
        except OSError:
            logger.info("Code de sortie du conteneur non écrit.")
        os.spawnv(os.P_NOWAIT, str(program), [str(program)])
        return
    exit_fn(code)


def schedule_container_stop(code: int = 1) -> None:
    """Return the HTTP response first, then stop."""
    timer = threading.Timer(_DELAY_SECONDS, lambda: container_stop(code))
    timer.daemon = True
    timer.start()
