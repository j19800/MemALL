"""File system watchdog for bridge daemon.

Watches inbox/outbox for new JSON message files using polling (cross-platform).
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class FileWatcher:
    """Poll a directory for new .json files and invoke callback."""

    def __init__(self, watch_dir: Path, callback: Callable[[dict, str], None],
                 interval: float = 1.0, agent_name: str = "?"):
        self.watch_dir = watch_dir
        self.callback = callback
        self.interval = max(0.2, interval)
        self.agent_name = agent_name
        self._seen: set[str] = set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._seed_seen()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True,
                                        name=f"fw-{self.agent_name}")
        self._thread.start()
        logger.info("filewatcher(%s) started poll=%s interval=%.1fs",
                     self.agent_name, self.watch_dir, self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _seed_seen(self) -> None:
        if not self.watch_dir.exists():
            return
        for f in sorted(self.watch_dir.glob("*.json")):
            self._seen.add(f.name)

    @staticmethod
    def _is_complete(path: Path) -> bool:
        """Check if a file is likely complete (not being written)."""
        try:
            return path.stat().st_size > 0
        except OSError:
            return False

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as e:
                logger.warning("filewatcher(%s) poll error: %s",
                               self.agent_name, e)
            time.sleep(self.interval)

    def _poll_once(self) -> None:
        if not self.watch_dir.exists():
            return
        for f in sorted(self.watch_dir.glob("*.json")):
            name = f.name
            if name in self._seen:
                continue
            try:
                # Only read and process complete files (skip files modified within last second)
                if not self._is_complete(f):
                    continue
                data = json.loads(f.read_text(encoding="utf-8"))
                self._seen.add(name)
                self.callback(data, name)
            except json.JSONDecodeError as e:
                logger.warning("filewatcher(%s) invalid json %s: %s",
                               self.agent_name, name, e)
            except Exception as e:
                logger.warning("filewatcher(%s) callback error %s: %s",
                               self.agent_name, name, e)
        if len(self._seen) > 2000:
            self._seen = set(list(self._seen)[-1000:])
