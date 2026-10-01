"""Acesso HTTP ao SAP Gateway (CATALOGSERVICE e $metadata).

Todas as funções são síncronas e sem dependência de Tk, para poderem rodar
em uma thread de trabalho.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from .logging_setup import log
from .services import extract_results

try:
    import requests
except ImportError:  # pragma: no cover - depende do ambiente
    requests = None

DEFAULT_TIMEOUT = 30

_HTTP_HINTS = {
    401: "Usuário ou senha SAP inválidos (HTTP 401).",
    403: "Usuário sem autorização para este recurso (HTTP 403).",
    404: "Recurso não encontrado no Gateway (HTTP 404). Verifique a URL.",
    500: "Erro interno no servidor SAP (HTTP 500). Veja /IWFND/ERROR_LOG.",
    503: "Serviço indisponível no Gateway (HTTP 503).",
}


class ODataClientError(Exception):
    """Erro amigável para exibir ao usuário."""


def requests_available() -> bool:
    return requests is not None


def _require_requests() -> None:
    if requests is None:
        raise ODataClientError(
            "A biblioteca 'requests' não está instalada.\n\n"
            "Instale com:\n\n    pip install requests"
        )


def _auth(username: str, password: str) -> Optional[Tuple[str, str]]:
    username = (username or "").strip()
    return (username, password or "") if username else None


def _get(url: str, username: str, password: str, verify_ssl: bool, timeout: int, accept: str):
    """GET com tratamento de erros traduzido para ODataClientError."""
    _require_requests()

    if not verify_ssl:
        try:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:  # pragma: no cover
            pass

    log.info("GET %s (verify_ssl=%s)", url, verify_ssl)
    try:
        resp = requests.get(
            url,
            auth=_auth(username, password),
            verify=verify_ssl,
            timeout=timeout,
            headers={"Accept": accept},
        )
    except requests.exceptions.SSLError as e:
        raise ODataClientError(
            "Falha na validação do certificado SSL.\n\n"
            "Se o servidor usa certificado autoassinado, marque "
            "'Ignorar certificado SSL'.\n\n" + str(e)
        ) from e
    except requests.exceptions.ConnectTimeout as e:
        raise ODataClientError(f"Tempo esgotado ao conectar em {url}.") from e
    except requests.exceptions.ReadTimeout as e:
        raise ODataClientError(
            f"O servidor demorou mais de {timeout}s para responder."
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise ODataClientError(
            f"Não foi possível conectar ao servidor:\n{url}\n\n{e}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise ODataClientError(str(e)) from e

    if resp.status_code >= 400:
        hint = _HTTP_HINTS.get(resp.status_code, f"HTTP {resp.status_code} {resp.reason}")
        raise ODataClientError(f"{hint}\n\nURL: {url}")
    return resp


def service_collection_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    return base + "/ServiceCollection?$format=json"


def fetch_service_collection(
    base_url: str,
    username: str = "",
    password: str = "",
    verify_ssl: bool = True,
    timeout: int = DEFAULT_TIMEOUT,
) -> Tuple[List[Dict[str, Any]], Any]:
    """Baixa o ServiceCollection. Retorna ``(results, json_bruto)``."""
    base_url = (base_url or "").strip()
    if not base_url:
        raise ODataClientError("Informe a URL base do CATALOGSERVICE.")

    url = service_collection_url(base_url)
    resp = _get(url, username, password, verify_ssl, timeout, "application/json")

    try:
        data = resp.json()
    except (ValueError, json.JSONDecodeError) as e:
        raise ODataClientError(
            "A resposta do servidor não é JSON.\n"
            "Verifique se o CATALOGSERVICE está acessível e se o parâmetro "
            "?$format=json foi aceito."
        ) from e

    results = extract_results(data)
    log.info("ServiceCollection retornou %d registros.", len(results))
    return results, data


def fetch_metadata(
    metadata_url: str,
    username: str = "",
    password: str = "",
    verify_ssl: bool = True,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Baixa o XML do $metadata e retorna o texto."""
    if not metadata_url:
        raise ODataClientError("Não foi possível montar a URL do $metadata.")
    resp = _get(metadata_url, username, password, verify_ssl, timeout, "application/xml")
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        # Gateway raramente declara charset; EDMX é UTF-8 por padrão.
        resp.encoding = resp.apparent_encoding or "utf-8"
    text = resp.text
    log.info("$metadata recebido (%d caracteres).", len(text))
    return text
