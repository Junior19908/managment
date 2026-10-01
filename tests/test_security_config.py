import hashlib
import json
import os
from datetime import datetime, timedelta

from catalog_viewer.config import AppConfig, DEFAULT_BASE_URL
from catalog_viewer.security import (
    hash_password,
    needs_rehash,
    new_session_expiry,
    session_is_valid,
    verify_password,
)


# ------------------------------------------------------------- security


def test_pbkdf2_roundtrip_and_salt():
    h1 = hash_password("segredo")
    h2 = hash_password("segredo")
    assert h1 != h2  # salt aleatório
    assert h1.startswith("pbkdf2_sha256$")
    assert verify_password("segredo", h1)
    assert not verify_password("errada", h1)
    assert not needs_rehash(h1)


def test_legacy_sha256_is_accepted_and_flagged_for_rehash():
    legacy = hashlib.sha256("1234".encode()).hexdigest()
    assert verify_password("1234", legacy)
    assert not verify_password("4321", legacy)
    assert needs_rehash(legacy)


def test_verify_handles_garbage():
    assert not verify_password("x", None)
    assert not verify_password("x", "")
    assert not verify_password("x", "algo$sem$formato")
    assert not verify_password("x", "pbkdf2_sha256$abc$zz$zz")
    assert needs_rehash("quebrado")


def test_session_validity():
    now = datetime(2025, 1, 1, 12, 0, 0)
    exp = new_session_expiry(hours=24, now=now)
    assert session_is_valid(exp, now=now)
    assert session_is_valid(exp, now=now + timedelta(hours=23))
    assert not session_is_valid(exp, now=now + timedelta(hours=25))
    assert not session_is_valid(None)
    assert not session_is_valid("data-invalida")


# --------------------------------------------------------------- config


def test_config_defaults_when_missing(tmp_path):
    cfg = AppConfig.load(str(tmp_path / "nope.json"))
    assert cfg.base_url == DEFAULT_BASE_URL
    assert cfg.ignore_ssl is False
    assert cfg.data_source == "local"


def test_config_roundtrip_preserves_unknown_keys(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(
        json.dumps(
            {
                "base_url": " https://h/x ",
                "username": "usr",
                "ignore_ssl": 1,
                "data_source": "invalida",
                "request_timeout": "abc",
                "futuro": {"a": 1},
            }
        ),
        encoding="utf-8",
    )
    cfg = AppConfig.load(str(path))
    assert cfg.base_url == "https://h/x"
    assert cfg.ignore_ssl is True
    assert cfg.data_source == "local"  # sanitizado
    assert cfg.request_timeout == 30
    assert cfg.extra == {"futuro": {"a": 1}}

    cfg.last_selected_id = "X_0001"
    cfg.save(str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["last_selected_id"] == "X_0001"
    assert data["futuro"] == {"a": 1}
    assert "extra" not in data
    assert not os.path.exists(str(path) + ".tmp")


def test_config_invalid_json_falls_back(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text("{ nao é json", encoding="utf-8")
    assert AppConfig.load(str(path)).username == ""
    path.write_text("[1,2]", encoding="utf-8")
    assert AppConfig.load(str(path)).username == ""


def test_resolve_local_metadata_path_fallbacks(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "metadata.json").write_text("{}", encoding="utf-8")

    cfg = AppConfig()
    # relativo → resolvido a partir do app_dir
    cfg.local_metadata_path = "metadata.json"
    assert cfg.resolve_local_metadata_path(str(app_dir)) == str(app_dir / "metadata.json")

    # absoluto inexistente (config de outra máquina) → mesmo nome ao lado do app
    cfg.local_metadata_path = "C:/ProjetosDev/outro/metadata.json"
    assert cfg.resolve_local_metadata_path(str(app_dir)) == str(app_dir / "metadata.json")

    # absoluto inexistente sem fallback → devolve o próprio caminho
    cfg.local_metadata_path = "C:/nada/x.json"
    assert cfg.resolve_local_metadata_path(str(app_dir)) == "C:/nada/x.json"


def test_set_local_metadata_path_stores_relative_inside_app_dir(tmp_path):
    cfg = AppConfig()
    inside = str(tmp_path / "sub" / "m.json")
    cfg.set_local_metadata_path(inside, str(tmp_path))
    assert cfg.local_metadata_path == "sub/m.json"

    outside = str(tmp_path.parent / "fora.json")
    cfg.set_local_metadata_path(outside, str(tmp_path))
    assert cfg.local_metadata_path == outside

    cfg.set_local_metadata_path("", str(tmp_path))
    assert cfg.local_metadata_path == "metadata.json"
