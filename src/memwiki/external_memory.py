from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from html import escape
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from memwiki.ids import sha256_bytes, stable_id, utc_now
from memwiki.locking import workspace_lock
from memwiki.manifest import append_jsonl, read_jsonl
from memwiki.workspace import Workspace


class ExternalEntityKind(str, Enum):
    AGENT = "agent"
    PLUGIN = "plugin"
    SKILL = "skill"
    MCP_SERVER = "mcp_server"
    API = "api"
    APP = "app"
    CONNECTOR = "connector"
    SERVICE = "service"
    DATASET = "dataset"
    DOCUMENT = "document"
    REPOSITORY = "repository"
    WEBSITE = "website"


class ExternalRelationshipKind(str, Enum):
    USES = "uses"
    PROVIDES = "provides"
    PRODUCED = "produced"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    INFORMS = "informs"
    DEPENDS_ON = "depends_on"
    DERIVED_FROM = "derived_from"
    REFERENCES = "references"
    REFERENCED_BY = "referenced_by"
    DOCUMENTS = "documents"
    IMPLEMENTS = "implements"
    IMPLEMENTED_BY = "implemented_by"
    MONITORS = "monitors"
    SYNCHRONIZES_WITH = "synchronizes_with"
    SUPERSEDES = "supersedes"


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]*$")
_CREDENTIAL_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "credential",
    "credentials",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}


def _safe_json(value: object, label: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{label} keys must be strings")
            if key.lower().replace("-", "_") in _CREDENTIAL_KEYS:
                raise ValueError(f"credential material is forbidden in {label}")
            _safe_json(item, f"{label}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _safe_json(item, f"{label}[{index}]")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"{label} must be JSON-compatible")


def _nonempty(value: str, label: str) -> str:
    if not value.strip():
        raise ValueError(f"{label} must be non-empty")
    return value


@dataclass(frozen=True)
class ExternalEntity:
    entity_id: str
    revision: int
    name: str
    kind: ExternalEntityKind
    locator: str
    version: str
    publisher: str
    capabilities: Tuple[str, ...]
    metadata: Mapping[str, Any]
    source_id: Optional[str]
    recorded_at: str
    previous_revision_hash: Optional[str]
    revision_hash: str

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["kind"] = self.kind.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalEntity":
        data = dict(value)
        data["kind"] = ExternalEntityKind(str(data["kind"]))
        data["capabilities"] = tuple(str(item) for item in data["capabilities"])
        return cls(**data)


@dataclass(frozen=True)
class ExternalRelationship:
    relationship_id: str
    revision: int
    from_id: str
    to_id: str
    relationship: ExternalRelationshipKind
    evidence: Mapping[str, Any]
    recorded_at: str
    previous_revision_hash: Optional[str]
    revision_hash: str

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["relationship"] = self.relationship.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalRelationship":
        data = dict(value)
        data["relationship"] = ExternalRelationshipKind(str(data["relationship"]))
        return cls(**data)


class ExternalMemoryCatalog:
    """Append-only external entity and cross-wiki relationship memory."""

    def __init__(self, workspace: Workspace) -> None:
        workspace.require()
        self.workspace = workspace
        self.entities_path = workspace.path("manifests/external-entities.jsonl")
        self.relationships_path = workspace.path("manifests/external-relationships.jsonl")
        self.internal_path = workspace.path(".memwiki/external-internal-objects.jsonl")

    def register(
        self,
        *,
        name: str,
        kind: ExternalEntityKind,
        locator: str,
        version: str,
        publisher: str,
        capabilities: Sequence[str],
        metadata: Mapping[str, Any] = {},
        source_id: Optional[str] = None,
    ) -> ExternalEntity:
        for value, label in [(name, "name"), (locator, "locator"), (version, "version"), (publisher, "publisher")]:
            _nonempty(value, label)
        if not capabilities or any(not item.strip() for item in capabilities):
            raise ValueError("capabilities must contain non-empty values")
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("capabilities must be unique")
        _safe_json(metadata, "external entity metadata")
        if source_id is not None:
            self._require_object(source_id)
        with workspace_lock(self.workspace, exclusive=True):
            current = self._current_entities()
            if any(item.locator == locator for item in current.values()):
                raise ValueError("external locator is already registered")
            entity_id = stable_id("ext", sha256_bytes(locator.encode("utf-8")), kind.value)
            entity = self._entity(
                entity_id=entity_id,
                revision=1,
                name=name,
                kind=kind,
                locator=locator,
                version=version,
                publisher=publisher,
                capabilities=tuple(capabilities),
                metadata=dict(metadata),
                source_id=source_id,
                previous_revision_hash=None,
            )
            append_jsonl(self.entities_path, entity.to_dict())
            return entity

    def update(
        self,
        entity_id: str,
        *,
        expected_revision: int,
        name: Optional[str] = None,
        version: Optional[str] = None,
        capabilities: Optional[Sequence[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        source_id: Optional[str] = None,
    ) -> ExternalEntity:
        with workspace_lock(self.workspace, exclusive=True):
            current = self._current_entities().get(entity_id)
            if current is None:
                raise KeyError(f"unknown external entity: {entity_id}")
            if current.revision != expected_revision:
                raise ValueError("external entity revision conflict")
            next_metadata = current.metadata if metadata is None else dict(metadata)
            _safe_json(next_metadata, "external entity metadata")
            next_capabilities = current.capabilities if capabilities is None else tuple(capabilities)
            if not next_capabilities or len(set(next_capabilities)) != len(next_capabilities):
                raise ValueError("capabilities must be non-empty and unique")
            if source_id is not None:
                self._require_object(source_id)
            entity = self._entity(
                entity_id=current.entity_id,
                revision=current.revision + 1,
                name=_nonempty(name if name is not None else current.name, "name"),
                kind=current.kind,
                locator=current.locator,
                version=_nonempty(version if version is not None else current.version, "version"),
                publisher=current.publisher,
                capabilities=next_capabilities,
                metadata=next_metadata,
                source_id=source_id if source_id is not None else current.source_id,
                previous_revision_hash=current.revision_hash,
            )
            append_jsonl(self.entities_path, entity.to_dict())
            return entity

    def resolve(self, entity_id: str) -> ExternalEntity:
        try:
            return self._current_entities()[entity_id]
        except KeyError:
            raise KeyError(f"unknown external entity: {entity_id}") from None

    def history(self, entity_id: str) -> Tuple[ExternalEntity, ...]:
        values = tuple(item for item in self._entities() if item.entity_id == entity_id)
        if not values:
            raise KeyError(f"unknown external entity: {entity_id}")
        self._verify_entity_chain(values)
        return values

    def register_internal_object(self, object_id: str, kind: str, metadata: Mapping[str, Any]) -> None:
        if not _IDENTIFIER.fullmatch(object_id) or not _IDENTIFIER.fullmatch(kind):
            raise ValueError("internal object identifier and kind must be normalized")
        _safe_json(metadata, "internal object metadata")
        existing = read_jsonl(self.internal_path)
        if any(item.get("object_id") == object_id for item in existing):
            raise ValueError("internal object is already registered")
        append_jsonl(self.internal_path, {"object_id": object_id, "kind": kind, "metadata": dict(metadata)})

    def relate(
        self,
        from_id: str,
        to_id: str,
        relationship: ExternalRelationshipKind,
        *,
        evidence: Mapping[str, Any] = {},
    ) -> ExternalRelationship:
        self._require_object(from_id)
        self._require_object(to_id)
        if from_id == to_id:
            raise ValueError("relationship endpoints must differ")
        _safe_json(evidence, "relationship evidence")
        relationship_id = stable_id("rel", sha256_bytes(f"{from_id}\0{to_id}".encode()), "external")
        if relationship_id in self._current_relationships():
            raise ValueError("relationship endpoints are already connected")
        value = self._relationship(
            relationship_id=relationship_id,
            revision=1,
            from_id=from_id,
            to_id=to_id,
            relationship=relationship,
            evidence=dict(evidence),
            previous_revision_hash=None,
        )
        append_jsonl(self.relationships_path, value.to_dict())
        return value

    def update_relationship(
        self,
        relationship_id: str,
        *,
        expected_revision: int,
        relationship: ExternalRelationshipKind,
        evidence: Mapping[str, Any],
    ) -> ExternalRelationship:
        current = self.resolve_relationship(relationship_id)
        if current.revision != expected_revision:
            raise ValueError("external relationship revision conflict")
        _safe_json(evidence, "relationship evidence")
        value = self._relationship(
            relationship_id=current.relationship_id,
            revision=current.revision + 1,
            from_id=current.from_id,
            to_id=current.to_id,
            relationship=relationship,
            evidence=dict(evidence),
            previous_revision_hash=current.revision_hash,
        )
        append_jsonl(self.relationships_path, value.to_dict())
        return value

    def resolve_relationship(self, relationship_id: str) -> ExternalRelationship:
        try:
            return self._current_relationships()[relationship_id]
        except KeyError:
            raise KeyError(f"unknown external relationship: {relationship_id}") from None

    def relationship_history(self, relationship_id: str) -> Tuple[ExternalRelationship, ...]:
        values = tuple(item for item in self._relationships() if item.relationship_id == relationship_id)
        if not values:
            raise KeyError(f"unknown external relationship: {relationship_id}")
        return values

    def neighbors(self, object_id: str) -> Tuple[ExternalRelationship, ...]:
        return tuple(
            sorted(
                (item for item in self._current_relationships().values() if item.from_id == object_id),
                key=lambda item: item.relationship_id,
            )
        )

    def backlinks(self, object_id: str) -> Tuple[ExternalRelationship, ...]:
        return tuple(
            sorted(
                (item for item in self._current_relationships().values() if item.to_id == object_id),
                key=lambda item: item.relationship_id,
            )
        )

    def find_path(self, from_id: str, to_id: str, max_depth: int = 8) -> Tuple[ExternalRelationship, ...]:
        queue: List[Tuple[str, Tuple[ExternalRelationship, ...]]] = [(from_id, ())]
        visited = {from_id}
        while queue:
            node, path = queue.pop(0)
            if len(path) >= max_depth:
                continue
            for link in self.neighbors(node):
                candidate = path + (link,)
                if link.to_id == to_id:
                    return candidate
                if link.to_id not in visited:
                    visited.add(link.to_id)
                    queue.append((link.to_id, candidate))
        return ()

    def impact(self, object_id: str, max_depth: int = 4) -> Tuple[Dict[str, Any], ...]:
        queue: List[Tuple[str, int]] = [(object_id, 0)]
        visited = {object_id}
        result: List[Dict[str, Any]] = []
        while queue:
            node, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            for link in self.neighbors(node):
                if link.to_id in visited:
                    continue
                visited.add(link.to_id)
                result.append({"object_id": link.to_id, "depth": depth + 1, "via": link.relationship.value})
                queue.append((link.to_id, depth + 1))
        return tuple(result)

    def explain(self, from_id: str, to_id: str) -> str:
        path = self.find_path(from_id, to_id)
        if not path:
            return f"No directed relationship path from {from_id} to {to_id}."
        parts = [path[0].from_id]
        for link in path:
            parts.extend([f"--{link.relationship.value}-->", link.to_id])
        return " ".join(parts)

    def graph_index(self) -> Dict[str, Any]:
        entities = [
            item.to_dict() for item in sorted(self._current_entities().values(), key=lambda item: item.entity_id)
        ]
        relationships = [
            item.to_dict()
            for item in sorted(self._current_relationships().values(), key=lambda item: item.relationship_id)
        ]
        return {"external_entities": entities, "external_relationships": relationships}

    def render_html(self) -> str:
        graph = self.graph_index()
        entities = cast_entities(graph["external_entities"])
        relationships = cast_relationships(graph["external_relationships"])
        json_ld = {
            "@context": "https://schema.org",
            "@type": "Dataset",
            "name": "External source and project relationship memory",
            "variableMeasured": ["external entities", "project relationships"],
            "externalEntities": entities,
            "externalRelationships": relationships,
        }
        json_ld_text = json.dumps(json_ld, sort_keys=True).replace("<", "\\u003c").replace(">", "\\u003e")
        entity_rows = (
            "".join(
                "<tr>"
                f"<td><code>{escape(str(item['entity_id']))}</code></td>"
                f"<td>{escape(str(item['name']))}</td>"
                f"<td>{escape(str(item['kind']))}</td>"
                f"<td>{escape(str(item['version']))}</td>"
                f"<td><code>{escape(str(item['locator']))}</code></td>"
                "</tr>"
                for item in entities
            )
            or '<tr><td colspan="5">No external entities recorded.</td></tr>'
        )
        relationship_rows = (
            "".join(
                "<tr>"
                f"<td><code>{escape(str(item['from_id']))}</code></td>"
                f"<td>{escape(str(item['relationship']))}</td>"
                f"<td><code>{escape(str(item['to_id']))}</code></td>"
                "</tr>"
                for item in relationships
            )
            or '<tr><td colspan="3">No relationships recorded.</td></tr>'
        )
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>External Memory Relationships</title>"
            f'<script type="application/ld+json">{json_ld_text}</script>'
            "</head><body><main><h1>External Memory Relationships</h1>"
            "<section><h2>External Entities</h2><table><thead><tr><th>ID</th><th>Name</th>"
            "<th>Kind</th><th>Version</th><th>Locator</th></tr></thead><tbody>"
            f"{entity_rows}</tbody></table></section>"
            "<section><h2>Relationships To Project Memory</h2><table><thead><tr><th>From</th>"
            "<th>Relationship</th><th>To</th></tr></thead><tbody>"
            f"{relationship_rows}</tbody></table></section></main></body></html>"
        )

    def _entities(self) -> Tuple[ExternalEntity, ...]:
        return tuple(ExternalEntity.from_dict(item) for item in read_jsonl(self.entities_path))

    def _relationships(self) -> Tuple[ExternalRelationship, ...]:
        return tuple(ExternalRelationship.from_dict(item) for item in read_jsonl(self.relationships_path))

    def _current_entities(self) -> Dict[str, ExternalEntity]:
        current: Dict[str, ExternalEntity] = {}
        for item in self._entities():
            if item.revision_hash != _revision_digest(item.to_dict()):
                raise ValueError("external entity revision hash failed")
            previous = current.get(item.entity_id)
            if previous is not None and item.previous_revision_hash != previous.revision_hash:
                raise ValueError("external entity revision chain failed")
            current[item.entity_id] = item
        return current

    def _current_relationships(self) -> Dict[str, ExternalRelationship]:
        current: Dict[str, ExternalRelationship] = {}
        for item in self._relationships():
            if item.revision_hash != _revision_digest(item.to_dict()):
                raise ValueError("external relationship revision hash failed")
            previous = current.get(item.relationship_id)
            if previous is not None and item.previous_revision_hash != previous.revision_hash:
                raise ValueError("external relationship revision chain failed")
            current[item.relationship_id] = item
        return current

    def _require_object(self, object_id: str) -> None:
        if object_id in self._current_entities():
            return
        if any(item.get("object_id") == object_id for item in read_jsonl(self.internal_path)):
            return
        manifest_keys = [("sources", "source_id"), ("pages", "page_id"), ("claims", "claim_id")]
        for manifest, key in manifest_keys:
            if any(
                item.get(key) == object_id for item in read_jsonl(self.workspace.path(f"manifests/{manifest}.jsonl"))
            ):
                return
        raise KeyError(f"unknown relationship object: {object_id}")

    @staticmethod
    def _entity(**values: Any) -> ExternalEntity:
        unsigned = {**values, "recorded_at": utc_now()}
        digest = sha256_bytes(
            json.dumps({**unsigned, "kind": unsigned["kind"].value}, sort_keys=True, separators=(",", ":")).encode()
        )
        return ExternalEntity(**unsigned, revision_hash=digest)

    @staticmethod
    def _relationship(**values: Any) -> ExternalRelationship:
        unsigned = {**values, "recorded_at": utc_now()}
        digest = sha256_bytes(
            json.dumps(
                {**unsigned, "relationship": unsigned["relationship"].value}, sort_keys=True, separators=(",", ":")
            ).encode()
        )
        return ExternalRelationship(**unsigned, revision_hash=digest)

    @staticmethod
    def _verify_entity_chain(values: Sequence[ExternalEntity]) -> None:
        previous = None
        for item in values:
            if item.revision_hash != _revision_digest(item.to_dict()):
                raise ValueError("external entity revision hash failed")
            if item.previous_revision_hash != previous:
                raise ValueError("external entity revision chain failed")
            previous = item.revision_hash


def cast_entities(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("external entity graph must be a list")
    return [dict(item) for item in value]


def cast_relationships(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("external relationship graph must be a list")
    return [dict(item) for item in value]


def _revision_digest(value: Mapping[str, Any]) -> str:
    unsigned = {key: item for key, item in value.items() if key != "revision_hash"}
    return sha256_bytes(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode())
