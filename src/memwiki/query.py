from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Dict, List, Tuple, TypedDict

from bs4 import BeautifulSoup

from memwiki.html import render_page
from memwiki.ids import sha256_bytes, stable_id, utc_now
from memwiki.manifest import read_jsonl
from memwiki.policy import OperationContext, append_event, is_clinical_phi, require_operation_context
from memwiki.workspace import Workspace


class SearchEntry(TypedDict):
    path: str
    title: str
    tokens: Counter[str]
    summary: str


@dataclass(frozen=True)
class QueryMatch:
    path: str
    title: str
    summary: str
    score: int

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class QueryResult:
    question: str
    matches: List[QueryMatch]
    citations: List[str]
    answer: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "question": self.question,
            "matches": [match.to_dict() for match in self.matches],
            "citations": self.citations,
            "answer": self.answer,
        }


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def build_search_index(workspace: Workspace) -> Dict[str, SearchEntry]:
    index: Dict[str, SearchEntry] = {}
    for path in workspace.path("wiki").glob("*.html"):
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        text = soup.get_text(" ", strip=True)
        index[path.name] = {
            "path": f"wiki/{path.name}",
            "title": soup.title.string if soup.title and soup.title.string else path.stem,
            "tokens": Counter(_tokens(text)),
            "summary": text[:500],
        }
    workspace.path(".memwiki/index").mkdir(parents=True, exist_ok=True)
    serializable = {key: {**value, "tokens": dict(value["tokens"])} for key, value in index.items()}
    workspace.path(".memwiki/index/search.json").write_text(
        json.dumps(serializable, indent=2, sort_keys=True), encoding="utf-8"
    )
    return index


def _score(query_tokens: List[str], page_tokens: Counter[str]) -> int:
    return sum(page_tokens.get(token, 0) for token in query_tokens)


def _clinical_query(workspace: Workspace, question: str, draft_page: bool) -> QueryResult:
    claims = read_jsonl(workspace.path("manifests/claims.jsonl"))
    fact_claims = [claim for claim in claims if claim.get("clinical_claim_type") == "source_fact"]
    guidance_claims = [claim for claim in claims if claim.get("clinical_claim_type") == "clinical_guidance"]
    if not fact_claims and not guidance_claims:
        return QueryResult(
            question=question,
            matches=[],
            citations=[],
            answer="No matching clinical wiki claims found.",
        )
    index = build_search_index(workspace)
    query_tokens = _tokens(question)
    ranked: List[Tuple[int, str, SearchEntry]] = sorted(
        ((_score(query_tokens, value["tokens"]), key, value) for key, value in index.items()),
        key=lambda item: item[0],
        reverse=True,
    )
    matches = [item for item in ranked if item[0] > 0][:3]
    query_matches = [
        QueryMatch(path=page["path"], title=page["title"], summary=page["summary"], score=score)
        for score, _, page in matches
    ]
    citations = sorted(
        {
            str(claim["source_id"])
            for claim in fact_claims + guidance_claims
            if isinstance(claim.get("source_id"), str)
        }
    )
    lines = [f"Answer for: {question}", "", "Evidence summary"]
    for claim in fact_claims[:5]:
        lines.append(f"- {claim['text']} [source: {claim['source_id']}]")
    lines.extend(["", "Decision support"])
    for claim in guidance_claims[:5]:
        cited = ", ".join(str(value) for value in claim.get("cited_claim_ids", []))
        lines.append(f"- {claim['text']} [status: {claim['review_status']}; cited claims: {cited}]")
    if citations:
        lines.extend(["", "Citations: " + ", ".join(citations)])
    answer = "\n".join(lines)
    if draft_page:
        draft_id = stable_id("draft", "query", question, utc_now())
        draft_root = workspace.path(f"drafts/{draft_id}/wiki")
        draft_root.mkdir(parents=True, exist_ok=True)
        page_id = stable_id("page", "query", question)
        html = render_page(
            title=f"Query: {question}",
            page_id=page_id,
            page_type="concept",
            body=f"<section id=\"answer\"><h2>Answer</h2><pre>{answer}</pre></section>",
            metadata={"memwiki:queryHash": sha256_bytes(question.encode("utf-8"))},
        )
        (draft_root / f"{page_id}.html").write_text(html, encoding="utf-8")
    return QueryResult(question=question, matches=query_matches, citations=citations, answer=answer)


def query_workspace_structured(
    workspace: Workspace,
    question: str,
    draft_page: bool = False,
    context: OperationContext | None = None,
) -> QueryResult:
    workspace.require()
    require_operation_context(workspace.config_path, "query", context)
    if is_clinical_phi(workspace.config_path):
        result = _clinical_query(workspace, question, draft_page)
        append_event(
            workspace.root,
            "query",
            {
                "question_sha256": sha256_bytes(question.encode("utf-8")),
                "matches": len(result.matches),
                "citations": result.citations,
            },
            context,
        )
        return result
    index = build_search_index(workspace)
    query_tokens = _tokens(question)
    ranked: List[Tuple[int, str, SearchEntry]] = sorted(
        ((_score(query_tokens, value["tokens"]), key, value) for key, value in index.items()),
        key=lambda item: item[0],
        reverse=True,
    )
    matches = [item for item in ranked if item[0] > 0][:3]
    if not matches:
        append_event(
            workspace.root,
            "query",
            {"question_sha256": sha256_bytes(question.encode("utf-8")), "matches": 0, "citations": []},
            context,
        )
        return QueryResult(
            question=question,
            matches=[],
            citations=[],
            answer="No matching wiki pages found.",
        )
    lines = [f"Answer for: {question}", ""]
    source_ids = {
        source_id
        for record in read_jsonl(workspace.path("manifests/sources.jsonl"))
        for source_id in [record.get("source_id")]
        if isinstance(source_id, str)
    }
    query_matches: List[QueryMatch] = []
    for score, _, page in matches:
        lines.append(f"- {page['title']} ({page['path']}): {page['summary']}")
        query_matches.append(
            QueryMatch(
                path=page["path"],
                title=page["title"],
                summary=page["summary"],
                score=score,
            )
        )
    claims = read_jsonl(workspace.path("manifests/claims.jsonl"))
    cited_sources = sorted(
        {
            source_id
            for claim in claims
            for source_id in [claim.get("source_id")]
            if isinstance(source_id, str) and source_id in source_ids
        }
    )
    citations = list(cited_sources)
    if citations:
        lines.append("")
        lines.append("Citations: " + ", ".join(citations))
    answer = "\n".join(lines)
    if draft_page:
        draft_id = stable_id("draft", "query", question, utc_now())
        draft_root = workspace.path(f"drafts/{draft_id}/wiki")
        draft_root.mkdir(parents=True, exist_ok=True)
        page_id = stable_id("page", "query", question)
        html = render_page(
            title=f"Query: {question}",
            page_id=page_id,
            page_type="concept",
            body=f"<section id=\"answer\"><h2>Answer</h2><pre>{answer}</pre></section>",
            metadata={"memwiki:query": question},
        )
        (draft_root / f"{page_id}.html").write_text(html, encoding="utf-8")
    append_event(
        workspace.root,
        "query",
        {
            "question_sha256": sha256_bytes(question.encode("utf-8")),
            "matches": len(query_matches),
            "citations": citations,
        },
        context,
    )
    return QueryResult(question=question, matches=query_matches, citations=citations, answer=answer)


def query_workspace(
    workspace: Workspace,
    question: str,
    draft_page: bool = False,
    context: OperationContext | None = None,
) -> str:
    return query_workspace_structured(workspace, question, draft_page=draft_page, context=context).answer
