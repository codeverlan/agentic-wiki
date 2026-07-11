from pathlib import Path

import pytest

from memwiki.external_memory import (
    ExternalEntityKind,
    ExternalMemoryCatalog,
    ExternalRelationshipKind,
)
from memwiki.init import init_workspace


def catalog(tmp_path: Path) -> ExternalMemoryCatalog:
    return ExternalMemoryCatalog(init_workspace(tmp_path))


def test_external_entity_can_be_registered_edited_and_resolved_with_history(tmp_path: Path) -> None:
    memory = catalog(tmp_path)
    first = memory.register(
        name="SEO Decision Lab",
        kind=ExternalEntityKind.PLUGIN,
        locator="plugin://seo-decision-lab@personal/",
        version="0.1.0+codex.one",
        publisher="NicoSKOOL",
        capabilities=("keyword_research", "technical_audit"),
        metadata={"product": "codex", "visibility": "personal_private"},
    )
    second = memory.update(
        first.entity_id,
        expected_revision=1,
        version="0.1.0+codex.two",
        metadata={"product": "codex", "visibility": "personal_private", "status": "ready"},
    )

    assert second.revision == 2
    assert memory.resolve(first.entity_id) == second
    assert [item.revision for item in memory.history(first.entity_id)] == [1, 2]
    assert memory.history(first.entity_id)[0].version == "0.1.0+codex.one"


def test_optimistic_revision_and_identity_collisions_are_rejected(tmp_path: Path) -> None:
    memory = catalog(tmp_path)
    entity = memory.register(
        name="Search API",
        kind=ExternalEntityKind.API,
        locator="https://api.example.test/v1",
        version="1",
        publisher="Example",
        capabilities=("search",),
    )
    with pytest.raises(ValueError, match="revision"):
        memory.update(entity.entity_id, expected_revision=0, version="2")
    with pytest.raises(ValueError, match="already registered"):
        memory.register(
            name="Spoof",
            kind=ExternalEntityKind.API,
            locator="https://api.example.test/v1",
            version="1",
            publisher="Other",
            capabilities=("search",),
        )


def test_relationships_connect_external_internal_and_other_external_objects(tmp_path: Path) -> None:
    memory = catalog(tmp_path)
    plugin = memory.register(
        name="SEO Decision Lab",
        kind=ExternalEntityKind.PLUGIN,
        locator="plugin://seo-decision-lab@personal/",
        version="1",
        publisher="Local",
        capabilities=("seo",),
    )
    api = memory.register(
        name="DataForSEO",
        kind=ExternalEntityKind.API,
        locator="https://api.dataforseo.com/v3/",
        version="3",
        publisher="DataForSEO",
        capabilities=("seo_data",),
    )
    memory.register_internal_object("claim-seo-1", "claim", {"title": "Target keyword claim"})

    provides = memory.relate(
        plugin.entity_id,
        api.entity_id,
        ExternalRelationshipKind.USES,
        evidence={"surface": "local_mcp"},
    )
    supports = memory.relate(
        api.entity_id,
        "claim-seo-1",
        ExternalRelationshipKind.SUPPORTS,
        evidence={"request_hash": "sha256:abc"},
    )

    assert memory.neighbors(plugin.entity_id)[0] == provides
    assert supports in memory.backlinks("claim-seo-1")
    path = memory.find_path(plugin.entity_id, "claim-seo-1")
    assert [item.relationship for item in path] == [
        ExternalRelationshipKind.USES,
        ExternalRelationshipKind.SUPPORTS,
    ]


def test_relationship_update_is_append_only_and_can_be_superseded(tmp_path: Path) -> None:
    memory = catalog(tmp_path)
    source = memory.register(
        name="Dataset",
        kind=ExternalEntityKind.DATASET,
        locator="dataset://rankings/2026-07",
        version="2026-07",
        publisher="Project",
        capabilities=("ranking_evidence",),
    )
    memory.register_internal_object("page-seo", "page", {"title": "SEO plan"})
    link = memory.relate(source.entity_id, "page-seo", ExternalRelationshipKind.INFORMS)
    replaced = memory.update_relationship(
        link.relationship_id,
        expected_revision=1,
        relationship=ExternalRelationshipKind.SUPPORTS,
        evidence={"reviewed": True},
    )
    assert replaced.revision == 2
    assert memory.resolve_relationship(link.relationship_id) == replaced
    assert [item.relationship for item in memory.relationship_history(link.relationship_id)] == [
        ExternalRelationshipKind.INFORMS,
        ExternalRelationshipKind.SUPPORTS,
    ]


def test_graph_impact_and_relationship_explanations_are_deterministic(tmp_path: Path) -> None:
    memory = catalog(tmp_path)
    api = memory.register(
        name="Claims API",
        kind=ExternalEntityKind.API,
        locator="api://claims",
        version="1",
        publisher="Vendor",
        capabilities=("claims",),
    )
    for object_id, kind in [("claim-a", "claim"), ("page-a", "page"), ("slice-a", "slice")]:
        memory.register_internal_object(object_id, kind, {"title": object_id})
    memory.relate(api.entity_id, "claim-a", ExternalRelationshipKind.SUPPORTS)
    memory.relate("claim-a", "page-a", ExternalRelationshipKind.REFERENCED_BY)
    memory.relate("page-a", "slice-a", ExternalRelationshipKind.IMPLEMENTED_BY)

    impact = memory.impact(api.entity_id, max_depth=3)
    assert [item["object_id"] for item in impact] == ["claim-a", "page-a", "slice-a"]
    explanation = memory.explain(api.entity_id, "slice-a")
    assert "supports" in explanation
    assert "implemented_by" in explanation


@pytest.mark.parametrize(
    "metadata",
    [{"api_key": "bad"}, {"nested": {"password": "bad"}}, {"token": "bad"}],
)
def test_external_memory_never_stores_credentials(tmp_path: Path, metadata: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="credential"):
        catalog(tmp_path).register(
            name="Bad API",
            kind=ExternalEntityKind.API,
            locator="api://bad",
            version="1",
            publisher="Bad",
            capabilities=("bad",),
            metadata=metadata,
        )


def test_external_memory_renders_semantic_xss_safe_relationship_view(tmp_path: Path) -> None:
    memory = catalog(tmp_path)
    entity = memory.register(
        name="<script>alert(1)</script>",
        kind=ExternalEntityKind.SERVICE,
        locator="service://safe?<unsafe>",
        version="1",
        publisher="Vendor",
        capabilities=("lookup",),
    )
    memory.register_internal_object("page-safe", "page", {"title": "Safe"})
    memory.relate(entity.entity_id, "page-safe", ExternalRelationshipKind.REFERENCES)

    html = memory.render_html()

    assert "<!doctype html>" in html
    assert 'type="application/ld+json"' in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "references" in html


def test_external_memory_detects_revision_tampering(tmp_path: Path) -> None:
    memory = catalog(tmp_path)
    entity = memory.register(
        name="Trusted API",
        kind=ExternalEntityKind.API,
        locator="api://trusted",
        version="1",
        publisher="Vendor",
        capabilities=("lookup",),
    )
    path = memory.entities_path
    content = path.read_text(encoding="utf-8").replace('"version": "1"', '"version": "forged"')
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="revision hash"):
        memory.resolve(entity.entity_id)


def test_external_entity_source_reference_must_resolve_to_wiki_memory(tmp_path: Path) -> None:
    memory = catalog(tmp_path)
    with pytest.raises(KeyError, match="unknown relationship object"):
        memory.register(
            name="Unbound dataset",
            kind=ExternalEntityKind.DATASET,
            locator="dataset://unbound",
            version="1",
            publisher="Project",
            capabilities=("evidence",),
            source_id="src-missing",
        )
