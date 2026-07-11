from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from agentic_wiki import AgenticWikiWorkspace
from memwiki.manifest import read_jsonl, write_jsonl


def _compiled_wiki(tmp_path: Path) -> tuple[AgenticWikiWorkspace, str, str]:
    wiki = AgenticWikiWorkspace(tmp_path)
    wiki.init()
    source = tmp_path / "source.txt"
    source.write_text("Canonical promotion must preserve source integrity.", encoding="utf-8")
    ingested = wiki.ingest(source)
    draft = wiki.compile(ingested.source_id)
    return wiki, ingested.source_id, draft.draft_id


def test_promotion_rejects_tampered_raw_source(tmp_path: Path) -> None:
    wiki, source_id, draft_id = _compiled_wiki(tmp_path)
    source_record = read_jsonl(tmp_path / "manifests/sources.jsonl")[0]
    (tmp_path / str(source_record["raw_path"])).write_text("tampered", encoding="utf-8")

    with pytest.raises(ValueError, match=f"Raw source integrity check failed: {source_id}"):
        wiki.promote(draft_id)

    assert read_jsonl(tmp_path / "manifests/pages.jsonl") == []
    assert list((tmp_path / "wiki").glob("page_*.html")) == []


def test_promotion_rejects_tampered_extracted_source(tmp_path: Path) -> None:
    wiki, source_id, draft_id = _compiled_wiki(tmp_path)
    extracted = tmp_path / ".memwiki" / "extracted" / source_id / "text.txt"
    extracted.write_text("tampered", encoding="utf-8")

    with pytest.raises(ValueError, match=f"Extracted source integrity check failed: {source_id}"):
        wiki.promote(draft_id)

    assert read_jsonl(tmp_path / "manifests/pages.jsonl") == []


def test_failed_promotion_rolls_back_all_canonical_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki, _, draft_id = _compiled_wiki(tmp_path)
    from memwiki import promote as promote_module

    promoted_name = next((tmp_path / "drafts" / draft_id / "wiki").glob("*.html")).name

    original_copy = promote_module.shutil.copy2
    copy_count = 0

    def fail_after_first_copy(source: Path, destination: Path) -> None:
        nonlocal copy_count
        copy_count += 1
        original_copy(source, destination)
        if copy_count == 1:
            raise OSError("synthetic promotion failure")

    monkeypatch.setattr(promote_module.shutil, "copy2", fail_after_first_copy)

    with pytest.raises(OSError, match="synthetic promotion failure"):
        wiki.promote(draft_id)

    assert read_jsonl(tmp_path / "manifests/pages.jsonl") == []
    assert read_jsonl(tmp_path / "manifests/claims.jsonl") == []
    assert read_jsonl(tmp_path / "manifests/links.jsonl") == []
    assert not (tmp_path / "wiki" / promoted_name).exists()


def test_compile_rejects_tampered_extracted_source(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path)
    wiki.init()
    source = tmp_path / "source.txt"
    source.write_text("Source integrity is checked before compilation.", encoding="utf-8")
    ingested = wiki.ingest(source)
    extracted = tmp_path / ".memwiki" / "extracted" / ingested.source_id / "text.txt"
    extracted.write_text("tampered", encoding="utf-8")

    with pytest.raises(ValueError, match=f"Extracted source integrity check failed: {ingested.source_id}"):
        wiki.compile(ingested.source_id)


def test_repeated_promotion_is_idempotent(tmp_path: Path) -> None:
    wiki, _, draft_id = _compiled_wiki(tmp_path)

    first = wiki.promote(draft_id)
    second = wiki.promote(draft_id)

    assert second == first
    assert len(read_jsonl(tmp_path / "manifests/pages.jsonl")) == 1
    assert len(read_jsonl(tmp_path / "manifests/claims.jsonl")) == 1
    promote_events = [
        event for event in read_jsonl(tmp_path / "manifests/events.jsonl") if event.get("event_type") == "promote"
    ]
    assert len(promote_events) == 1


def test_concurrent_promotion_is_serialized_and_idempotent(tmp_path: Path) -> None:
    wiki, _, draft_id = _compiled_wiki(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: wiki.promote(draft_id), range(2)))

    assert results[0] == results[1]
    assert len(read_jsonl(tmp_path / "manifests/pages.jsonl")) == 1
    promote_events = [
        event for event in read_jsonl(tmp_path / "manifests/events.jsonl") if event.get("event_type") == "promote"
    ]
    assert len(promote_events) == 1


def test_source_integrity_rejects_manifest_path_escape(tmp_path: Path) -> None:
    wiki, _, draft_id = _compiled_wiki(tmp_path)
    records = read_jsonl(tmp_path / "manifests/sources.jsonl")
    records[0]["raw_path"] = str(tmp_path.parent / "outside.txt")
    write_jsonl(tmp_path / "manifests/sources.jsonl", records)

    with pytest.raises(ValueError, match="Source manifest does not match integrity ledger"):
        wiki.promote(draft_id)


def test_promotion_rolls_back_when_success_event_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki, _, draft_id = _compiled_wiki(tmp_path)
    from memwiki import promote as promote_module

    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for root in [tmp_path / "wiki", tmp_path / "docs", tmp_path / "manifests"]
        for path in root.rglob("*")
        if path.is_file()
    }

    def fail_event(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic event failure")

    monkeypatch.setattr(promote_module, "append_event", fail_event)

    with pytest.raises(OSError, match="synthetic event failure"):
        wiki.promote(draft_id)

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for root in [tmp_path / "wiki", tmp_path / "docs", tmp_path / "manifests"]
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_promotion_rejects_mutated_claim_identity_and_unsupported_text(tmp_path: Path) -> None:
    wiki, _, draft_id = _compiled_wiki(tmp_path)
    claims_path = tmp_path / "drafts" / draft_id / "manifests" / "claims.jsonl"
    claims = read_jsonl(claims_path)
    claims[0]["text"] = "Source claims an unrelated production fact."
    write_jsonl(claims_path, claims)

    with pytest.raises(ValueError, match="claim identity does not match|not supported by extracted source evidence"):
        wiki.promote(draft_id)


@pytest.mark.parametrize("locator", [None, {}, {"type": "json", "value": "../../outside.json"}])
def test_promotion_rejects_malformed_or_unsafe_source_locator(tmp_path: Path, locator: object) -> None:
    wiki, _, draft_id = _compiled_wiki(tmp_path)
    claims_path = tmp_path / "drafts" / draft_id / "manifests" / "claims.jsonl"
    claims = read_jsonl(claims_path)
    claims[0]["provenance"]["source_locator"] = locator
    write_jsonl(claims_path, claims)

    with pytest.raises(ValueError, match="source_locator"):
        wiki.promote(draft_id)


def test_canonical_reader_waits_for_promotion_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki, _, first_draft_id = _compiled_wiki(tmp_path)
    wiki.promote(first_draft_id)
    existing_page_id = str(read_jsonl(tmp_path / "manifests/pages.jsonl")[0]["page_id"])
    second_source = tmp_path / "second-source.txt"
    second_source.write_text("Second promotion generation.", encoding="utf-8")
    second_draft_id = wiki.compile(wiki.ingest(second_source).source_id).draft_id
    from memwiki import promote as promote_module

    started = Event()
    release = Event()
    original_copy = promote_module.shutil.copy2

    def paused_copy(source: Path, destination: Path) -> None:
        if second_draft_id in str(source):
            started.set()
            assert release.wait(timeout=2)
        original_copy(source, destination)

    monkeypatch.setattr(promote_module.shutil, "copy2", paused_copy)
    with ThreadPoolExecutor(max_workers=2) as executor:
        promotion = executor.submit(wiki.promote, second_draft_id)
        assert started.wait(timeout=2)
        reader = executor.submit(wiki.resolve, existing_page_id)
        assert not reader.done()
        release.set()
        assert promotion.result(timeout=2).promoted_pages == 1
        assert reader.result(timeout=2)["id"] == existing_page_id
