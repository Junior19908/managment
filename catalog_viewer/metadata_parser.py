"""Parser do XML EDMX ($metadata) de serviços OData v2/v4.

Independente de namespaces (compara apenas o nome local das tags) e lê
também as anotações SAP (``sap:label``, ``sap:creatable`` etc.).
"""

import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .logging_setup import log

_DOC_TAGS = ("Summary", "LongDescription", "Description")


class MetadataParseError(Exception):
    """XML inválido ou não é um EDMX."""


@dataclass
class PropertyInfo:
    name: str
    type: str = ""
    nullable: str = "true"
    max_length: str = ""
    is_key: bool = False
    label: str = ""
    doc: str = ""

    @property
    def display_doc(self) -> str:
        return self.doc or self.label


@dataclass
class NavigationPropertyInfo:
    name: str
    target: str = ""  # Relationship (v2) ou Type (v4)


@dataclass
class EntityTypeInfo:
    full_name: str
    name: str
    namespace: str = ""
    base_type: str = ""
    label: str = ""
    doc: str = ""
    keys: List[str] = field(default_factory=list)
    properties: List[PropertyInfo] = field(default_factory=list)
    navigation: List[NavigationPropertyInfo] = field(default_factory=list)

    @property
    def display_doc(self) -> str:
        return self.doc or self.label


@dataclass
class EntitySetInfo:
    name: str
    entity_type: str = ""
    label: str = ""
    doc: str = ""
    creatable: bool = True
    updatable: bool = True
    deletable: bool = True

    @property
    def display_doc(self) -> str:
        return self.doc or self.label

    @property
    def crud_flags(self) -> str:
        """Ex.: ``C U D`` → criável, atualizável, deletável."""
        return " ".join(
            flag if enabled else "-"
            for flag, enabled in (("C", self.creatable), ("U", self.updatable), ("D", self.deletable))
        )


@dataclass
class FunctionImportInfo:
    name: str
    return_type: str = ""
    http_method: str = ""
    parameters: List[str] = field(default_factory=list)


@dataclass
class MetadataModel:
    entity_sets: List[EntitySetInfo] = field(default_factory=list)
    entity_types: List[EntityTypeInfo] = field(default_factory=list)
    function_imports: List[FunctionImportInfo] = field(default_factory=list)
    namespaces: List[str] = field(default_factory=list)
    edmx_version: str = ""

    def entity_type_by_name(self, name: str) -> Optional[EntityTypeInfo]:
        """Busca por nome completo ou simples."""
        for et in self.entity_types:
            if et.full_name == name:
                return et
        short = name.rsplit(".", 1)[-1]
        for et in self.entity_types:
            if et.name == short:
                return et
        return None

    @property
    def entity_types_by_name(self) -> Dict[str, EntityTypeInfo]:
        return {et.full_name: et for et in self.entity_types}


# ------------------------------------------------------------------ helpers


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _attr(el: ET.Element, name: str, default: str = "") -> str:
    """Lê atributo pelo nome local, ignorando o namespace (ex.: ``sap:label``)."""
    if name in el.attrib:
        return el.attrib[name]
    for key, value in el.attrib.items():
        if _local(key) == name:
            return value
    return default


def _flag(el: ET.Element, name: str, default: bool = True) -> bool:
    value = _attr(el, name, "").strip().lower()
    if value in ("true", "1", "x"):
        return True
    if value in ("false", "0", ""):
        return default if value == "" else False
    return default


def _documentation(el: ET.Element) -> str:
    for child in el:
        if _local(child.tag) != "Documentation":
            continue
        for sub in child:
            if _local(sub.tag) in _DOC_TAGS and sub.text and sub.text.strip():
                return sub.text.strip()
    return ""


def _parse_property(el: ET.Element, keys: List[str]) -> PropertyInfo:
    name = el.attrib.get("Name", "")
    return PropertyInfo(
        name=name,
        type=el.attrib.get("Type", ""),
        nullable=el.attrib.get("Nullable", "true"),
        max_length=el.attrib.get("MaxLength", ""),
        is_key=name in keys,
        label=_attr(el, "label") or _attr(el, "quickinfo"),
        doc=_documentation(el),
    )


def _parse_entity_type(el: ET.Element, namespace: str) -> EntityTypeInfo:
    simple = el.attrib.get("Name", "")
    keys: List[str] = []
    for child in el:
        if _local(child.tag) == "Key":
            keys.extend(
                ref.attrib.get("Name", "") for ref in child if _local(ref.tag) == "PropertyRef"
            )

    info = EntityTypeInfo(
        full_name=f"{namespace}.{simple}" if namespace else simple,
        name=simple,
        namespace=namespace,
        base_type=el.attrib.get("BaseType", ""),
        label=_attr(el, "label"),
        doc=_documentation(el),
        keys=keys,
    )
    for child in el:
        tag = _local(child.tag)
        if tag == "Property":
            info.properties.append(_parse_property(child, keys))
        elif tag == "NavigationProperty":
            info.navigation.append(
                NavigationPropertyInfo(
                    name=child.attrib.get("Name", ""),
                    target=child.attrib.get("Relationship") or child.attrib.get("Type", ""),
                )
            )
    return info


def _parse_entity_set(el: ET.Element) -> EntitySetInfo:
    return EntitySetInfo(
        name=el.attrib.get("Name", ""),
        entity_type=el.attrib.get("EntityType", ""),
        label=_attr(el, "label"),
        doc=_documentation(el),
        creatable=_flag(el, "creatable"),
        updatable=_flag(el, "updatable"),
        deletable=_flag(el, "deletable"),
    )


def _parse_function_import(el: ET.Element) -> FunctionImportInfo:
    return FunctionImportInfo(
        name=el.attrib.get("Name", ""),
        return_type=el.attrib.get("ReturnType", "") or el.attrib.get("EntitySet", ""),
        http_method=_attr(el, "HttpMethod") or _attr(el, "Method"),
        parameters=[
            f"{p.attrib.get('Name', '')}:{p.attrib.get('Type', '')}"
            for p in el
            if _local(p.tag) == "Parameter"
        ],
    )


# ------------------------------------------------------------------ API


def parse_metadata(xml_text: str) -> MetadataModel:
    """Interpreta o EDMX e retorna o modelo. Levanta MetadataParseError se inválido."""
    if not xml_text or not xml_text.strip():
        raise MetadataParseError("O conteúdo do $metadata está vazio.")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise MetadataParseError(f"XML inválido: {e}") from e

    model = MetadataModel(edmx_version=_attr(root, "Version"))

    for schema in root.iter():
        if _local(schema.tag) != "Schema":
            continue
        ns = schema.attrib.get("Namespace", "")
        if ns:
            model.namespaces.append(ns)

        for el in schema:
            tag = _local(el.tag)
            if tag == "EntityType":
                model.entity_types.append(_parse_entity_type(el, ns))
            elif tag == "EntityContainer":
                for item in el:
                    itag = _local(item.tag)
                    if itag == "EntitySet":
                        model.entity_sets.append(_parse_entity_set(item))
                    elif itag == "FunctionImport":
                        model.function_imports.append(_parse_function_import(item))

    if not model.entity_types and not model.entity_sets and _local(root.tag) != "Edmx":
        raise MetadataParseError(
            f"O XML não parece ser um EDMX (raiz <{_local(root.tag)}>)."
        )

    log.info(
        "Parse do $metadata: %d EntitySets, %d EntityTypes, %d FunctionImports.",
        len(model.entity_sets),
        len(model.entity_types),
        len(model.function_imports),
    )
    return model


def pretty_print_xml(xml_text: str) -> str:
    """Reindenta o XML preservando prefixos de namespace. Retorna o original se falhar."""
    try:
        for _, (prefix, uri) in ET.iterparse(io.StringIO(xml_text), events=("start-ns",)):
            if prefix:
                ET.register_namespace(prefix, uri)
        root = ET.fromstring(xml_text)
        ET.indent(root, space="  ")
        body = ET.tostring(root, encoding="unicode")
        return '<?xml version="1.0" encoding="utf-8"?>\n' + body
    except ET.ParseError as e:
        log.warning("Falha ao formatar XML: %s", e)
        return xml_text
