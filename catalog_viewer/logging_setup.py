"""Configuração de log em arquivo com rotação automática."""

import logging
import os
from logging.handlers import RotatingFileHandler

LOGGER_NAME = "catalog_viewer"
LOG_FILE_NAME = "catalog_viewer.log"
MAX_BYTES = 1 * 1024 * 1024  # 1 MB por arquivo
BACKUP_COUNT = 3

log = logging.getLogger(LOGGER_NAME)


def setup_logging(app_dir: str, level: int = logging.INFO) -> str:
    """Configura o logger da aplicação gravando em ``app_dir``.

    Retorna o caminho completo do arquivo de log. Chamadas repetidas não
    duplicam handlers.
    """
    log_path = os.path.join(app_dir, LOG_FILE_NAME)
    if log.handlers:
        return log_path

    log.setLevel(level)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")

    try:
        handler = RotatingFileHandler(
            log_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
    except OSError:
        # Sem permissão de escrita: cai para o console.
        handler = logging.StreamHandler()
    handler.setFormatter(fmt)
    log.addHandler(handler)
    return log_path
