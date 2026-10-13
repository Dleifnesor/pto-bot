import logging
import logging.handlers
import sys
from collections import deque
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_configured = False
_log_file: Path | None = None


def setup(log_dir: Path, level: int = logging.INFO) -> None:
    global _configured, _log_file
    if _configured:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    _log_file = log_dir / "pto.log"
    root = logging.getLogger()
    root.setLevel(level)
    fh = logging.handlers.RotatingFileHandler(_log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter(_FORMAT))
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter(_FORMAT, "%H:%M:%S"))
    root.addHandler(fh)
    root.addHandler(sh)
    for noisy in ("httpx", "httpcore", "apscheduler.executors", "PIL", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def tail(n: int = 300) -> list[str]:
    if not _log_file or not _log_file.exists():
        return []
    with _log_file.open("r", encoding="utf-8", errors="replace") as f:
        return [line.rstrip("\n") for line in deque(f, maxlen=n)]


def get(name: str) -> logging.Logger:
    return logging.getLogger(name)
