import json
from pathlib import Path

from memwiki.external_memory import ExternalEntityKind, ExternalMemoryCatalog, ExternalRelationshipKind
from memwiki.graph import build_graph_index
from memwiki.init import init_workspace


def test_graph_index_combines_wiki_and_external_relationships(tmp_path: Path) -> None:
    workspace = init_workspace(tmp_path)
    catalog = ExternalMemoryCatalog(workspace)
    external = catalog.register(
        name="Research API",
        kind=ExternalEntityKind.API,
        locator="api://research",
        version="1",
        publisher="Vendor",
        capabilities=("research",),
    )
    catalog.register_internal_object("claim-1", "claim", {"title": "Claim"})
    catalog.relate(external.entity_id, "claim-1", ExternalRelationshipKind.SUPPORTS)
    workspace.path("manifests/links.jsonl").write_text(
        json.dumps({"from_id": "page-1", "to_id": "claim-1", "relationship": "contains_claim"}) + "\n",
        encoding="utf-8",
    )

    graph = build_graph_index(workspace)

    assert graph["links"] == [{"from_id": "page-1", "to_id": "claim-1", "relationship": "contains_claim"}]
    assert graph["external_relationships"][0]["from_id"] == external.entity_id
    assert graph["external_relationships"][0]["to_id"] == "claim-1"
    assert graph["external_entities"][0]["locator"] == "api://research"
    assert json.loads(workspace.path(".memwiki/index/graph.json").read_text()) == graph
