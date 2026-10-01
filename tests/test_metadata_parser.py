import re

import pytest

from catalog_viewer.metadata_parser import (
    MetadataParseError,
    parse_metadata,
    pretty_print_xml,
)

EDMX_V2 = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="1.0" xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx"
    xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
    xmlns:sap="http://www.sap.com/Protocols/SAPData">
  <edmx:DataServices m:DataServiceVersion="2.0">
    <Schema Namespace="ZSRV" xmlns="http://schemas.microsoft.com/ado/2008/09/edm">
      <EntityType Name="Order" sap:label="Ordem" sap:content-version="1">
        <Key><PropertyRef Name="OrderId"/></Key>
        <Property Name="OrderId" Type="Edm.String" Nullable="false" MaxLength="12" sap:label="Nº ordem"/>
        <Property Name="Descr" Type="Edm.String" MaxLength="40" sap:quickinfo="Texto">
          <Documentation><Summary>Descrição da ordem</Summary></Documentation>
        </Property>
        <NavigationProperty Name="ToItems" Relationship="ZSRV.OrderItems" FromRole="A" ToRole="B"/>
      </EntityType>
      <EntityType Name="Item">
        <Key><PropertyRef Name="OrderId"/><PropertyRef Name="Pos"/></Key>
        <Property Name="OrderId" Type="Edm.String" Nullable="false"/>
        <Property Name="Pos" Type="Edm.Int32" Nullable="false"/>
      </EntityType>
      <EntityContainer Name="ZSRV_Entities" m:IsDefaultEntityContainer="true">
        <EntitySet Name="OrderSet" EntityType="ZSRV.Order" sap:creatable="false" sap:deletable="false" sap:label="Ordens"/>
        <EntitySet Name="ItemSet" EntityType="ZSRV.Item">
          <Documentation><Summary>Itens</Summary></Documentation>
        </EntitySet>
        <FunctionImport Name="Release" ReturnType="ZSRV.Order" EntitySet="OrderSet" m:HttpMethod="POST">
          <Parameter Name="OrderId" Type="Edm.String" Mode="In"/>
        </FunctionImport>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""


def test_parse_entity_sets_with_sap_annotations():
    model = parse_metadata(EDMX_V2)
    assert model.namespaces == ["ZSRV"]
    assert model.edmx_version == "1.0"

    names = [es.name for es in model.entity_sets]
    assert names == ["OrderSet", "ItemSet"]

    order_set = model.entity_sets[0]
    assert order_set.entity_type == "ZSRV.Order"
    assert order_set.crud_flags == "- U -"
    assert order_set.display_doc == "Ordens"  # sap:label como fallback

    item_set = model.entity_sets[1]
    assert item_set.crud_flags == "C U D"
    assert item_set.display_doc == "Itens"  # Documentation tem prioridade


def test_parse_entity_types_keys_properties_navigation():
    model = parse_metadata(EDMX_V2)
    order = model.entity_type_by_name("ZSRV.Order")
    assert order is not None
    assert order.keys == ["OrderId"]
    assert order.display_doc == "Ordem"
    assert [p.name for p in order.properties] == ["OrderId", "Descr"]

    order_id, descr = order.properties
    assert order_id.is_key and order_id.nullable == "false" and order_id.max_length == "12"
    assert order_id.display_doc == "Nº ordem"
    assert descr.display_doc == "Descrição da ordem"
    assert not descr.is_key

    assert [n.target for n in order.navigation] == ["ZSRV.OrderItems"]

    item = model.entity_type_by_name("Item")  # busca por nome simples
    assert item is not None and item.keys == ["OrderId", "Pos"]


def test_parse_function_imports():
    model = parse_metadata(EDMX_V2)
    (fi,) = model.function_imports
    assert fi.name == "Release"
    assert fi.http_method == "POST"
    assert fi.return_type == "ZSRV.Order"
    assert fi.parameters == ["OrderId:Edm.String"]


def test_parse_errors():
    with pytest.raises(MetadataParseError):
        parse_metadata("")
    with pytest.raises(MetadataParseError):
        parse_metadata("<a><b></a>")
    with pytest.raises(MetadataParseError):
        parse_metadata("<html><body>login</body></html>")


def test_pretty_print_preserves_prefixes_and_content():
    ugly = re.sub(r">\s+<", "><", EDMX_V2)
    pretty = pretty_print_xml(ugly)
    assert "<edmx:Edmx" in pretty
    assert 'sap:label="Ordem"' in pretty
    assert pretty.count("\n") > 10
    # continua parseável e equivalente
    assert len(parse_metadata(pretty).entity_sets) == 2
    assert pretty_print_xml("<x>") == "<x>"  # inválido: devolve o original
