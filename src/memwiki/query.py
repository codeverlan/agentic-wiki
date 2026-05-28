from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Dict, List, Tuple, TypedDict

from bs4 import BeautifulSoup

from memwiki.html import render_page
from memwiki.ids import stable_id, utc_now
from memwiki.manifest import read_jsonl
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


def query_workspace_structured(workspace: Workspace, question: str, draft_page: bool = False) -> QueryResult:
    workspace.require()
    index = build_search_index(workspace)
    query_tokens = _tokens(question)
    ranked: List[Tuple[int, str, SearchEntry]] = sorted(
        ((_score(query_tokens, value["tokens"]), key, value) for key, value in index.items()),
        key=lambda item: item[0],
        reverse=True,
    )
    matches = [item for item in ranked if item[0] > 0][:3]
    if not matches:
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
    return QueryResult(question=question, matches=query_matches, citations=citations, answer=answer)


def query_workspace(workspace: Workspace, question: str, draft_page: bool = False) -> str:
    return query_workspace_structured(workspace, question, draft_page=draft_page).answer
