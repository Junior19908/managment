"""Persistência da configuração do aplicativo (JSON ao lado do app.py)."""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional

from .logging_setup import log

CONFIG_FILE_NAME = "catalog_viewer_config.json"
DEFAULT_BASE_URL = "https://seu-servidor:porta/sap/opu/odata/IWFND/CATALOGSERVICE;v=2"
DEFAULT_LOCAL_METADATA = "metadata.json"

_KNOWN_KEYS = {
    "base_url",
    "username",
    "ignore_ssl",
    "data_source",
    "local_metadata_path",
    "last_selected_tech_name",
    "last_selected_id",
    "window_geometry",
    "session_expires_at",
    "app_password_hash",
    "request_timeout",
}


@dataclass
class AppConfig:
    base_url: str = DEFAULT_BASE_URL
    username: str = ""
    ignore_ssl: bool = False
    data_source: str = "local"  # "local" ou "online"
    local_metadata_path: str = DEFAULT_LOCAL_METADATA
    last_selected_tech_name: Optional[str] = None
    last_selected_id: Optional[str] = None
    window_geometry: Optional[str] = None
    session_expires_at: Optional[str] = None
    app_password_hash: Optional[str] = None
    request_timeout: int = 30
    # Chaves desconhecidas são preservadas para não perder dados de versões futuras.
    extra: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ IO

    @classmethod
    def load(cls, path: str) -> "AppConfig":
        cfg = cls()
        if not os.path.exists(path):
            log.info("Arquivo de configuração não encontrado (%s); usando defaults.", path)
            return cfg
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError) as e:
            log.warning("Falha ao carregar configuração: %s", e)
            return cfg
        if not isinstance(raw, dict):
            log.warning("Configuração inválida (não é objeto JSON); usando defaults.")
            return cfg

        for key, value in raw.items():
            if key in _KNOWN_KEYS:
                setattr(cfg, key, value)
            else:
                cfg.extra[key] = value

        cfg._sanitize()
        log.info("Configuração carregada de %s", path)
        return cfg

    def save(self, path: str) -> None:
        """Grava de forma atômica (arquivo temporário + os.replace)."""
        data = asdict(self)
        extra = data.pop("extra", {})
        data.update(extra)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            log.debug("Configuração salva em %s", path)
        except OSError as e:
            log.error("Falha ao salvar configuração: %s", e)
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ------------------------------------------------------------ helpers

    def _sanitize(self) -> None:
        if self.data_source not in ("local", "online"):
            self.data_source = "local"
        self.ignore_ssl = bool(self.ignore_ssl)
        self.base_url = (self.base_url or DEFAULT_BASE_URL).strip()
        self.username = (self.username or "").strip()
        if not self.local_metadata_path:
            self.local_metadata_path = DEFAULT_LOCAL_METADATA
        try:
            self.request_timeout = max(5, int(self.request_timeout))
        except (TypeError, ValueError):
            self.request_timeout = 30

    def resolve_local_metadata_path(self, app_dir: str) -> str:
        """Resolve o caminho do metadata local.

        Caminhos relativos são resolvidos a partir de ``app_dir``. Se um caminho
        absoluto não existir mais (config copiada de outra máquina), tenta o
        arquivo de mesmo nome ao lado do app.
        """
        raw = (self.local_metadata_path or DEFAULT_LOCAL_METADATA).strip()
        path = raw if os.path.isabs(raw) else os.path.join(app_dir, raw)
        if os.path.exists(path):
            return path
        fallback = os.path.join(app_dir, os.path.basename(raw))
        if os.path.exists(fallback):
            log.info("Caminho '%s' não existe; usando '%s'.", path, fallback)
            return fallback
        return path

    def set_local_metadata_path(self, path: str, app_dir: str) -> None:
        """Guarda o caminho de forma relativa quando ele está dentro de ``app_dir``."""
        path = (path or "").strip()
        if not path:
            self.local_metadata_path = DEFAULT_LOCAL_METADATA
            return
        try:
            rel = os.path.relpath(path, app_dir)
            if not rel.startswith(".."):
                path = rel.replace("\\", "/")
        except ValueError:
            pass  # drives diferentes no Windows
        self.local_metadata_path = path
