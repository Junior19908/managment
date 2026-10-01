"""Normalização, filtro e exportação dos registros do ServiceCollection."""

import csv
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from .logging_setup import log

Service = Dict[str, Any]

_SAP_DATE_RE = re.compile(r"/Date\((-?\d+)(?:[+-]\d+)?\)/")

# (cabeçalho no CSV, chave no registro normalizado)
CSV_COLUMNS = [
    ("TechnicalServiceName", "TechnicalServiceName"),
    ("Version", "Version"),
    ("ServiceType", "ServiceType"),
    ("ServiceDescription", "ServiceDescription"),
    ("ServicePath", "ServicePath"),
    ("ServiceUrlCompleta", "ServiceUrl"),
    ("Author", "Author"),
    ("UpdatedDate", "UpdatedDateFormatted"),
]


def extract_results(data: Any) -> List[Service]:
    """Aceita ``{"d": {"results": [...]}}``, ``{"results": [...]}``, ``{"value": [...]}`` ou lista."""
    if isinstance(data, dict):
        d = data.get("d")
        if isinstance(d, dict) and isinstance(d.get("results"), list):
            return d["results"]
        if isinstance(d, list):
            return d
        if isinstance(data.get("results"), list):
            return data["results"]
        if isinstance(data.get("value"), list):  # OData v4
            return data["value"]
        return []
    if isinstance(data, list):
        return data
    return []


def parse_sap_date(value: Any) -> Optional[datetime]:
    """Converte ``/Date(1734353767000)/`` (ms desde epoch, UTC) em datetime local."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    m = _SAP_DATE_RE.fullmatch(value.strip())
    if m:
        try:
            ts = int(m.group(1)) / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def format_sap_date(value: Any, fmt: str = "%d/%m/%Y %H:%M") -> str:
    dt = parse_sap_date(value)
    return dt.strftime(fmt) if dt else ""


def normalize_services(results: Iterable[Any]) -> List[Service]:
    """Garante que todo registro tenha as chaves usadas pela UI.

    - Version (str)
    - ServiceDescription
    - ServicePath (derivado de ServiceUrl/MetadataUrl quando ausente)
    - ServiceType, Author, UpdatedDateFormatted
    """
    normalized: List[Service] = []
    for svc in results:
        if not isinstance(svc, dict):
            continue
        svc = dict(svc)

        version = svc.get("Version")
        if version in (None, ""):
            version = svc.get("TechnicalServiceVersion")
        if isinstance(version, float) and version.is_integer():
            version = int(version)
        svc["Version"] = "" if version is None else str(version)

        if not svc.get("ServiceDescription"):
            svc["ServiceDescription"] = svc.get("Description") or svc.get("Title") or ""

        if not svc.get("ServicePath"):
            source_url = svc.get("ServiceUrl") or svc.get("MetadataUrl") or ""
            path = ""
            if source_url:
                path = urlparse(source_url).path
                if path.lower().endswith("/$metadata"):
                    path = path[: -len("/$metadata")]
            svc["ServicePath"] = path

        svc["TechnicalServiceName"] = str(svc.get("TechnicalServiceName") or "")
        svc["ServiceType"] = str(svc.get("ServiceType") or "")
        svc["Author"] = str(svc.get("Author") or "")
        svc["UpdatedDateFormatted"] = format_sap_date(svc.get("UpdatedDate"))
        normalized.append(svc)

    log.info("Normalização concluída: %d serviços.", len(normalized))
    return normalized


def build_service_url(svc: Service, base_url: str = "") -> str:
    """URL completa do serviço: ServiceUrl, ou raiz do Gateway + ServicePath."""
    service_url = svc.get("ServiceUrl")
    if service_url:
        return str(service_url)

    service_path = str(svc.get("ServicePath") or "")
    if service_path.startswith(("http://", "https://")):
        return service_path

    parsed = urlparse((base_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return service_path

    root = f"{parsed.scheme}://{parsed.netloc}"
    if not service_path:
        return root
    return root + ("" if service_path.startswith("/") else "/") + service_path


def metadata_url_for(service_url: str) -> str:
    url = (service_url or "").strip()
    if not url:
        return ""
    if url.lower().endswith("$metadata"):
        return url
    return url.rstrip("/") + "/$metadata"


_SEARCH_FIELDS = (
    "TechnicalServiceName",
    "ServiceDescription",
    "ServicePath",
    "ServiceType",
    "Author",
    "ID",
    "Title",
)


def filter_services(services: Iterable[Service], query: str) -> List[Service]:
    """Filtro case-insensitive; todas as palavras da consulta devem aparecer."""
    terms = [t for t in (query or "").lower().split() if t]
    if not terms:
        return list(services)
    out = []
    for svc in services:
        haystack = " ".join(str(svc.get(f) or "") for f in _SEARCH_FIELDS).lower()
        if all(t in haystack for t in terms):
            out.append(svc)
    return out


def find_service_index(
    services: List[Service], service_id: Optional[str], tech_name: Optional[str]
) -> Optional[int]:
    """Localiza um serviço pelo ID (preferência) ou pelo nome técnico."""
    if service_id:
        for i, svc in enumerate(services):
            if svc.get("ID") == service_id:
                return i
    if tech_name:
        for i, svc in enumerate(services):
            if svc.get("TechnicalServiceName") == tech_name:
                return i
    return None


def export_services_csv(services: Iterable[Service], path: str, base_url: str = "") -> int:
    """Grava CSV (``;`` como separador, UTF-8 com BOM para o Excel). Retorna linhas gravadas."""
    count = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writerow([header for header, _ in CSV_COLUMNS])
        for svc in services:
            row = []
            for _, key in CSV_COLUMNS:
                if key == "ServiceUrl":
                    row.append(build_service_url(svc, base_url))
                else:
                    row.append(str(svc.get(key) or ""))
            writer.writerow(row)
            count += 1
    log.info("CSV exportado (%d linhas) para %s", count, path)
    return count
