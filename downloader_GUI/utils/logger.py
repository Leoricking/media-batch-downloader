import logging
import os
from config import DATA_DIR

_LOGGERS = {}


def get_logger(name: str) -> logging.Logger:
    if name in _LOGGERS:
        return _LOGGERS[name]

    os.makedirs(DATA_DIR, exist_ok=True)
    log_path = os.path.join(DATA_DIR, "downloader.log")

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        formatter = logging.Formatter(
            "[%(name)s] %(levelname)s: %(message)s"
        )

        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        logger.addHandler(sh)

    _LOGGERS[name] = logger
    return logger