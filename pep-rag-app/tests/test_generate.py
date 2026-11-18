"""Offline tests for prompt building, citation parsing and answer resolution."""

from dataclasses import dataclass

import pytest

from peptalk.generate import (
    DEFAULT_MODEL,
    PROXY_PLACEHOLDER_KEY,
    LLMConfigError,
    answer_question,
    ask,
    build_prompt,
    llm_config_from_env,
    parse_citations,
    resolve_citations,
    source_label,
)


@dataclass
class Passage:
    pep: int
    title: str
    section: str | None
    text: str
    chunk_id: str = ""


PASSAGES = [
    Passage(572, "Assignment Expressions", "Rationale", "Assignment expressions let you bind names inside expressions."),
    Passage(572, "Assignment Expressions", "Specification", "The operator is spelled :=."),
    Passage(8, "Style Guide for Python Code", None, "Use four spaces per indentation level."),
]


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        return self.reply


# ---------------------------------------------------------------- config


def test_config_requires_api_key():
    with pytest.raises(LLMConfigError):
        llm_config_from_env({})
    with pytest.raises(LLMConfigError):
        llm_config_from_env({"ANTHROPIC_API_KEY": "   "})


def test_config_proxy_route_needs_no_real_key():
    cfg = llm_config_from_env({"AGENSH_MODEL_BASE": "http://gitea:3000/proxy"})
    assert cfg.base_url == "http://gitea:3000/proxy"
    assert cfg.api_key == PROXY_PLACEHOLDER_KEY


def test_config_real_key_wins_over_proxy_placeholder():
    cfg = llm_config_from_env({"AGENSH_MODEL_BASE": "http://p", "ANTHROPIC_API_KEY": "sk-test"})
    assert cfg.api_key == "sk-test"


def test_config_defaults():
    cfg = llm_config_from_env({"ANTHROPIC_API_KEY": "x"})
    assert cfg.model == DEFAULT_MODEL == "claude-haiku-5-5"
    assert cfg.base_url is None
    assert cfg.workspace_id is None


def test_config_base_url_precedence():
    both = {"ANTHROPIC_API_KEY": "x", "AGENSH_MODEL_BASE": "http://a", "ANTHROPIC_BASE_URL": "http://b"}
    assert llm_config_from_env(both).base_url == "http://a"
    only_std = {"ANTHROPIC_API_KEY": "x", "ANTHROPIC_BASE_URL": "http://b"}
    assert llm_config_from_env(only_std).base_url == "http://b"


def test_config_reads_model_and_workspace():
    cfg = llm_config_from_env(
        {"ANTHROPIC_API_KEY": "x", "RAG_MODEL": "claude-x", "ANTHROPIC_WORKSPACE_ID": "ws-1"}
    )
    assert cfg.model == "claude-x"
    assert cfg.workspace_id == "ws-1"


# ---------------------------------------------------------------- prompt


def test_source_label():
    assert source_label(572, "Rationale") == "PEP 572 §Rationale"
    assert source_label(8, None) == "PEP 8"


def test_build_prompt_numbers_sources_and_includes_question():
    system, user = build_prompt("What is := ?", PASSAGES)
    assert "[1] PEP 572 §Rationale — Assignment Expressions" in user
    assert "[2] PEP 572 §Specification" in user
    assert "[3] PEP 8 — Style Guide for Python Code" in user
    assert user.rstrip().endswith("Question: What is := ?")
    assert "only" in system.lower() and "outside knowledge" in system.lower()


def test_build_prompt_with_no_passages_says_so():
    _, user = build_prompt("anything", [])
    assert "no sources were retrieved" in user


# ---------------------------------------------------------------- citations


def test_parse_single_and_multi_citations():
    text = "Walrus binds names [PEP 572 §Rationale]. Indent with spaces [PEP 8]."
    cites = parse_citations(text)
    assert [(c.pep, c.section) for c in cites] == [(572, "Rationale"), (8, None)]
    assert cites[0].raw == "[PEP 572 §Rationale]"


def test_parse_bracket_with_several_sources():
    cites = parse_citations("See [PEP 572 §Rationale; PEP 8 §Naming].")
    assert [(c.pep, c.section) for c in cites] == [(572, "Rationale"), (8, "Naming")]
    assert cites[0].start == cites[1].start


def test_parse_ignores_non_citation_brackets():
    assert parse_citations("A list [1, 2] and [see docs] are not citations.") == []


def test_resolve_keeps_valid_and_drops_invented_pep():
    answer = "It uses := [PEP 572 §Specification] and also [PEP 9999 §Foo]."
    resolved = resolve_citations(answer, PASSAGES)
    assert [p.pep for p in resolved.cited] == [572]
    assert resolved.dropped == ["[PEP 9999 §Foo]"]
    assert "9999" not in resolved.answer
    assert "[PEP 572 §Specification]" in resolved.answer


def test_resolve_partial_bracket_keeps_valid_items():
    answer = "Both apply [PEP 572 §Rationale; PEP 9999 §X]."
    resolved = resolve_citations(answer, PASSAGES)
    assert resolved.answer == "Both apply [PEP 572 §Rationale]."
    assert resolved.dropped == ["[PEP 572 §Rationale; PEP 9999 §X]"]


def test_resolve_matches_section_loosely_and_falls_back_to_pep():
    passages = [Passage(572, "t", "Rationale and Motivation", "x")]
    assert resolve_citations("a [PEP 572 §Rationale]", passages).cited == passages
    # unknown section on a retrieved PEP still counts as that PEP
    assert resolve_citations("a [PEP 572 §Nope]", passages).cited == passages


def test_resolve_deduplicates_cited_passages_in_order():
    answer = "x [PEP 8] y [PEP 572 §Rationale] z [PEP 572 §Rationale]"
    resolved = resolve_citations(answer, PASSAGES)
    assert [(p.pep, p.section) for p in resolved.cited] == [(8, None), (572, "Rationale")]


def test_resolve_no_citations_is_not_grounded():
    resolved = resolve_citations("The sources do not say.", PASSAGES)
    assert resolved.cited == []
    assert resolved.dropped == []


# ---------------------------------------------------------------- ask


def test_answer_question_uses_llm_and_resolves():
    llm = FakeLLM("Use := [PEP 572 §Specification]. Also [PEP 1234].")
    result = answer_question("What is :=?", PASSAGES, llm, mode="bm25", model="m")
    assert len(llm.calls) == 1
    assert result.grounded is True
    assert [p.section for p in result.cited] == ["Specification"]
    assert result.dropped_citations == ["[PEP 1234]"]
    assert result.mode == "bm25"
    assert result.model == "m"
    assert result.passages == PASSAGES


def test_answer_question_insufficient_evidence_is_not_grounded():
    llm = FakeLLM("The retrieved PEP text does not answer this question.")
    result = answer_question("Who won the match?", PASSAGES, llm)
    assert result.grounded is False
    assert result.cited == []


def test_ask_passes_mode_and_k_to_retriever():
    seen = {}

    def retrieve(question, mode, k):
        seen.update(question=question, mode=mode, k=k)
        return PASSAGES[:k]

    llm = FakeLLM("ok [PEP 8]")
    result = ask("  What is indentation?  ", retrieve, llm, mode="dense", k=1)
    assert seen == {"question": "What is indentation?", "mode": "dense", "k": 1}
    assert [p.pep for p in result.passages] == [572]
    assert result.grounded is False  # PEP 8 was not among the single retrieved passage


def test_ask_rejects_empty_question():
    with pytest.raises(ValueError):
        ask("   ", lambda q, m, k: [], FakeLLM("x"))
