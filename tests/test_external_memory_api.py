from pathlib import Path

import pytest

from agentic_wiki import (
    AgenticWikiWorkspace,
    ExternalEntity,
    ExternalEntityKind,
    ExternalEntityListResult,
    ExternalExplanationResult,
    ExternalImpactResult,
    ExternalMemoryViewResult,
    ExternalPathResult,
    ExternalRelationship,
    ExternalRelationshipKind,
    ExternalRelationshipListResult,
    MemwikiWorkspace,
    OperationContext,
)
from memwiki import AgenticWikiWorkspace as CompatibilityWorkspace
from memwiki.policy import PolicyError


def initialized(tmp_path: Path) -> AgenticWikiWorkspace:
    workspace = AgenticWikiWorkspace(tmp_path)
    workspace.init()
    return workspace


def register_api(workspace: AgenticWikiWorkspace, locator: str = "api://search") -> ExternalEntity:
    return workspace.register_external_entity(
        name="Search API",
        kind=ExternalEntityKind.API,
        locator=locator,
        version="1",
        publisher="Example",
        capabilities=("search",),
        metadata={"visibility": "private"},
    )


def test_public_packages_export_typed_external_memory_surface(tmp_path: Path) -> None:
    assert CompatibilityWorkspace is AgenticWikiWorkspace
    assert MemwikiWorkspace is AgenticWikiWorkspace

    entity = register_api(initialized(tmp_path))
    assert isinstance(entity, ExternalEntity)
    assert entity.to_dict()["kind"] == "api"


def test_entities_can_be_registered_updated_resolved_and_listed(tmp_path: Path) -> None:
    workspace = initialized(tmp_path)
    first = register_api(workspace)
    second = workspace.update_external_entity(
        first.entity_id,
        expected_revision=1,
        version="2",
        capabilities=("search", "rank"),
        metadata={"status": "ready"},
    )

    assert second.revision == 2
    assert workspace.resolve_external_entity(first.entity_id) == second
    listed = workspace.list_external_entities()
    assert isinstance(listed, ExternalEntityListResult)
    assert listed.entities == (second,)
    assert listed.to_dict()["entities"][0]["version"] == "2"


def test_relationship_api_connects_external_memory_to_wiki_objects(tmp_path: Path) -> None:
    workspace = initialized(tmp_path)
    entity = register_api(workspace)
    source = tmp_path / "source.txt"
    source.write_text("Search evidence", encoding="utf-8")
    ingested = workspace.ingest(source)

    first = workspace.relate_external_memory(
        entity.entity_id,
        ingested.source_id,
        ExternalRelationshipKind.SUPPORTS,
        evidence={"request_hash": "sha256:abc"},
    )
    assert isinstance(first, ExternalRelationship)
    second = workspace.update_external_relationship(
        first.relationship_id,
        expected_revision=1,
        relationship=ExternalRelationshipKind.INFORMS,
        evidence={"reviewed": True},
    )

    assert second.revision == 2
    neighbors = workspace.external_neighbors(entity.entity_id)
    backlinks = workspace.external_backlinks(ingested.source_id)
    assert isinstance(neighbors, ExternalRelationshipListResult)
    assert neighbors.relationships == (second,)
    assert backlinks.relationships == (second,)


def test_path_impact_and_explanation_are_typed_and_serializable(tmp_path: Path) -> None:
    workspace = initialized(tmp_path)
    first = register_api(workspace, "api://first")
    second = register_api(workspace, "api://second")
    third = register_api(workspace, "api://third")
    workspace.relate_external_memory(first.entity_id, second.entity_id, ExternalRelationshipKind.USES)
    workspace.relate_external_memory(second.entity_id, third.entity_id, ExternalRelationshipKind.SUPPORTS)

    path = workspace.external_path(first.entity_id, third.entity_id)
    impact = workspace.external_impact(first.entity_id)
    explanation = workspace.explain_external_relationship(first.entity_id, third.entity_id)

    assert isinstance(path, ExternalPathResult)
    assert len(path.relationships) == 2
    assert isinstance(impact, ExternalImpactResult)
    assert [item.object_id for item in impact.items] == [second.entity_id, third.entity_id]
    assert isinstance(explanation, ExternalExplanationResult)
    assert "supports" in explanation.explanation
    assert path.to_dict()["relationships"][0]["relationship"] == "uses"
    assert impact.to_dict()["items"][0]["depth"] == 1


def test_external_api_requires_context_in_clinical_workspace(tmp_path: Path) -> None:
    workspace = AgenticWikiWorkspace(tmp_path)
    workspace.init(
        profile="clinical_phi",
        client_record_id="client-synthetic-1",
        local_encrypted_storage_attested=True,
    )
    with pytest.raises(PolicyError, match="Operation context"):
        register_api(workspace)

    context = OperationContext(
        actor_id="tester",
        actor_role="developer",
        purpose_of_use="testing",
        session_id="s1",
    )
    entity = workspace.register_external_entity(
        name="Local synthetic API",
        kind=ExternalEntityKind.API,
        locator="api://local-synthetic",
        version="1",
        publisher="Project",
        capabilities=("synthetic",),
        context=context,
    )
    assert workspace.resolve_external_entity(entity.entity_id, context=context) == entity


def test_external_api_rejects_credentials_and_revision_conflicts(tmp_path: Path) -> None:
    workspace = initialized(tmp_path)
    entity = register_api(workspace)
    with pytest.raises(ValueError, match="credential"):
        workspace.update_external_entity(
            entity.entity_id,
            expected_revision=1,
            metadata={"api_key": "forbidden"},
        )
    with pytest.raises(ValueError, match="revision"):
        workspace.update_external_entity(entity.entity_id, expected_revision=9, version="2")


def test_external_memory_can_render_a_project_local_wiki_relationship_view(tmp_path: Path) -> None:
    workspace = initialized(tmp_path)
    entity = register_api(workspace)
    source = tmp_path / "evidence.txt"
    source.write_text("Evidence", encoding="utf-8")
    ingested = workspace.ingest(source)
    workspace.relate_external_memory(
        entity.entity_id,
        ingested.source_id,
        ExternalRelationshipKind.SUPPORTS,
    )

    result = workspace.render_external_memory()

    assert isinstance(result, ExternalMemoryViewResult)
    assert result.entities == 1
    assert result.relationships == 1
    html = Path(result.output).read_text(encoding="utf-8")
    assert "External Memory Relationships" in html
    assert 'type="application/ld+json"' in html
    assert entity.entity_id in html


def test_external_memory_view_is_confined_to_workspace(tmp_path: Path) -> None:
    workspace = initialized(tmp_path)
    with pytest.raises(ValueError, match="inside the workspace"):
        workspace.render_external_memory(tmp_path.parent / "outside.html")
