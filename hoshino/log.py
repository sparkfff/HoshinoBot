import logging
from logging.handlers import RotatingFileHandler
import os
import sys

os.makedirs('./log', exist_ok=True)
_error_log_file = os.path.expanduser('./log/error.log')
_critical_log_file = os.path.expanduser('./log/critical.log')

formatter = logging.Formatter('[%(asctime)s %(name)s] %(levelname)s: %(message)s')
default_handler = logging.StreamHandler(sys.stdout)
default_handler.setFormatter(formatter)
# Keep at most 10 MiB in the active file and five historical files per level.
error_handler = RotatingFileHandler(_error_log_file, maxBytes=10 * 1024 * 1024,
                                   backupCount=5, encoding='utf8')
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(formatter)
critical_handler = RotatingFileHandler(_critical_log_file, maxBytes=10 * 1024 * 1024,
                                      backupCount=5, encoding='utf8')
critical_handler.setLevel(logging.CRITICAL)
critical_handler.setFormatter(formatter)


def new_logger(name, debug=True):
    logger = logging.getLogger(name)
    logger.addHandler(default_handler)
    logger.addHandler(error_handler)
    logger.addHandler(critical_handler)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    return logger
