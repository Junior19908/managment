import csv
from datetime import datetime

from catalog_viewer.services import (
    build_service_url,
    export_services_csv,
    extract_results,
    filter_services,
    find_service_index,
    format_sap_date,
    metadata_url_for,
    normalize_services,
    parse_sap_date,
)

RAW = {
    "ID": "ZMM_SRV_0001",
    "TechnicalServiceName": "ZMM_SRV",
    "TechnicalServiceVersion": 1,
    "Description": "Serviço de compras",
    "Title": "MM_SRV",
    "ServiceUrl": "https://host:8001/sap/opu/odata/sap/ZMM_SRV",
    "MetadataUrl": "https://host:8001/sap/opu/odata/sap/ZMM_SRV/$metadata",
    "UpdatedDate": "/Date(1734353767000)/",
    "ServiceType": "UI",
    "Author": "DEVELOP",
}


def test_extract_results_variants():
    assert extract_results({"d": {"results": [1]}}) == [1]
    assert extract_results({"d": [1]}) == [1]
    assert extract_results({"results": [2]}) == [2]
    assert extract_results({"value": [3]}) == [3]
    assert extract_results([4]) == [4]
    assert extract_results({"x": 1}) == []
    assert extract_results("nada") == []


def test_normalize_fills_missing_fields():
    (svc,) = normalize_services([RAW, "ignorado"])
    assert svc["Version"] == "1"
    assert svc["ServiceDescription"] == "Serviço de compras"
    assert svc["ServicePath"] == "/sap/opu/odata/sap/ZMM_SRV"
    assert svc["UpdatedDateFormatted"]  # data formatada
    assert svc["Author"] == "DEVELOP"


def test_normalize_service_path_from_metadata_url():
    raw = dict(RAW)
    raw.pop("ServiceUrl")
    (svc,) = normalize_services([raw])
    assert svc["ServicePath"] == "/sap/opu/odata/sap/ZMM_SRV"


def test_normalize_keeps_existing_and_handles_float_version():
    (svc,) = normalize_services([{"Version": 2.0, "ServiceDescription": "x", "ServicePath": "/p"}])
    assert svc["Version"] == "2"  # float inteiro vira "2"
    (svc,) = normalize_services([{"TechnicalServiceVersion": 3.0}])
    assert svc["Version"] == "3"


def test_parse_sap_date():
    dt = parse_sap_date("/Date(1734353767000)/")
    assert isinstance(dt, datetime)
    assert dt.year == 2024
    assert parse_sap_date("/Date(1734353767000+0000)/") is not None
    assert parse_sap_date("2024-01-02T03:04:05") == datetime(2024, 1, 2, 3, 4, 5)
    assert parse_sap_date(None) is None
    assert parse_sap_date("lixo") is None
    assert format_sap_date("lixo") == ""


def test_build_service_url():
    assert build_service_url({"ServiceUrl": "https://a/b"}, "https://x") == "https://a/b"
    assert build_service_url({"ServicePath": "https://a/b"}, "") == "https://a/b"
    assert build_service_url({"ServicePath": "/sap/x"}, "https://h:1/sap/opu/odata/IWFND/CATALOGSERVICE;v=2") == "https://h:1/sap/x"
    assert build_service_url({"ServicePath": "sap/x"}, "https://h:1/") == "https://h:1/sap/x"
    assert build_service_url({"ServicePath": "/sap/x"}, "") == "/sap/x"
    assert build_service_url({"ServicePath": "/sap/x"}, "sem-esquema") == "/sap/x"


def test_metadata_url_for():
    assert metadata_url_for("https://a/b") == "https://a/b/$metadata"
    assert metadata_url_for("https://a/b/") == "https://a/b/$metadata"
    assert metadata_url_for("https://a/b/$metadata") == "https://a/b/$metadata"
    assert metadata_url_for("") == ""


def test_filter_services_multi_term_case_insensitive():
    services = normalize_services(
        [
            RAW,
            {**RAW, "ID": "Z2", "TechnicalServiceName": "ZSD_SRV", "Description": "Vendas"},
        ]
    )
    assert len(filter_services(services, "")) == 2
    assert [s["ID"] for s in filter_services(services, "compras")] == ["ZMM_SRV_0001"]
    assert [s["ID"] for s in filter_services(services, "zsd VENDAS")] == ["Z2"]
    assert filter_services(services, "zsd compras") == []


def test_find_service_index_prefers_id():
    services = [
        {"ID": "A_0001", "TechnicalServiceName": "A"},
        {"ID": "A_0002", "TechnicalServiceName": "A"},
    ]
    assert find_service_index(services, "A_0002", "A") == 1
    assert find_service_index(services, None, "A") == 0
    assert find_service_index(services, "nada", "nada") is None


def test_export_csv_quotes_delimiter(tmp_path):
    services = normalize_services([{**RAW, "Description": "a;b \"c\""}])
    path = tmp_path / "out.csv"
    assert export_services_csv(services, str(path)) == 1
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    assert rows[0][0] == "TechnicalServiceName"
    assert rows[1][3] == 'a;b "c"'
    assert rows[1][5] == RAW["ServiceUrl"]
