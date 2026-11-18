"""Answer generation: prompt building, citation parsing and the Claude call.

Everything here works on plain data. The model call sits behind the small
``LLM`` protocol, so tests can pass a fake and never touch the network.
The retrieval step is passed in as a callable that returns scored passages.

Citations use the form ``[PEP 572 §Rationale]``. A citation is kept only if
its PEP number is among the retrieved passages; anything else is removed from
the answer text and reported in ``AskResult.dropped_citations``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_MAX_TOKENS = 1024
# Non-secret stand-in for the key when the team proxy (AGENSH_MODEL_BASE) adds the credential.
PROXY_PLACEHOLDER_KEY = "agensh-proxy"

SYSTEM_PROMPT = """\
You answer questions about Python Enhancement Proposals (PEPs) using only the \
numbered sources that the user provides.

Rules:
- Use only facts stated in the sources. Do not use outside knowledge.
- Cite each factual claim inline with the PEP and section of the source it comes \
from, exactly in the form [PEP 572 §Rationale]. Several citations may sit side by \
side. Omit the section part only if the source has no section name.
- Only cite PEP numbers that appear in the sources.
- If the sources do not answer the question, say so in one sentence, for example \
"The retrieved PEP text does not answer this question.", and give no citations. \
Never guess.
- Be concise: one short paragraph or a few bullets.\
"""


class LLM(Protocol):
    """Anything that turns a system prompt and a user prompt into text."""

    def complete(self, system: str, user: str) -> str: ...


class LLMConfigError(RuntimeError):
    """Raised when the model route is not configured."""


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str | None
    model: str
    workspace_id: str | None


def llm_config_from_env(env: Mapping[str, str] | None = None) -> LLMConfig:
    """Read the model route from the environment.

    Base URL: ``AGENSH_MODEL_BASE``, else ``ANTHROPIC_BASE_URL``, else None (SDK default).
    Key: ``ANTHROPIC_API_KEY`` (must be non-empty). When ``AGENSH_MODEL_BASE`` is set and
    no key is given, a fixed placeholder is used: the team proxy adds the real credential,
    and the SDK refuses to send a request without any auth header. Model: ``RAG_MODEL``.
    """
    env = os.environ if env is None else env
    base_url = (env.get("AGENSH_MODEL_BASE") or env.get("ANTHROPIC_BASE_URL") or "").strip() or None
    api_key = (env.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        if not (env.get("AGENSH_MODEL_BASE") or "").strip():
            raise LLMConfigError("ANTHROPIC_API_KEY is not set")
        api_key = PROXY_PLACEHOLDER_KEY
    workspace_id = (env.get("ANTHROPIC_WORKSPACE_ID") or "").strip() or None
    model = (env.get("RAG_MODEL") or "").strip() or DEFAULT_MODEL
    return LLMConfig(api_key=api_key, base_url=base_url, model=model, workspace_id=workspace_id)


class AnthropicLLM:
    """``LLM`` backed by the official ``anthropic`` SDK."""

    def __init__(self, config: LLMConfig, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        import anthropic  # imported lazily so offline code paths do not need it

        kwargs: dict[str, Any] = {"api_key": config.api_key}
        if config.base_url:
            kwargs["base_url"] = config.base_url
        if config.workspace_id:
            kwargs["default_headers"] = {"anthropic-workspace-id": config.workspace_id}
        self.client = anthropic.Anthropic(**kwargs)
        self.model = config.model
        self.max_tokens = max_tokens

    def complete(self, system: str, user: str) -> str:
        message = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )


def llm_from_env(env: Mapping[str, str] | None = None) -> AnthropicLLM:
    return AnthropicLLM(llm_config_from_env(env))


# ---------------------------------------------------------------- prompting


def source_label(pep: int, section: str | None) -> str:
    """The citation form for a source, e.g. ``PEP 572 §Rationale``."""
    return f"PEP {pep} §{section}" if section else f"PEP {pep}"


def build_prompt(question: str, passages: Sequence[Any]) -> tuple[str, str]:
    """Return ``(system, user)`` prompts. Passages are numbered from 1 in the given order.

    Each passage needs ``pep``, ``title``, ``section`` and ``text`` attributes.
    """
    blocks = []
    for number, passage in enumerate(passages, start=1):
        header = f"[{number}] {source_label(passage.pep, passage.section)}"
        if passage.title:
            header += f" — {passage.title}"
        blocks.append(f"{header}\n{passage.text.strip()}")
    sources = "\n\n".join(blocks) if blocks else "(no sources were retrieved)"
    user = f"Sources:\n\n{sources}\n\nQuestion: {question.strip()}"
    return SYSTEM_PROMPT, user


# ---------------------------------------------------------------- citations

_BRACKET = re.compile(r"\[(PEP\s*\d+[^\[\]\n]*)\]")
_ITEM_SPLIT = re.compile(r"[;,](?=\s*PEP\s*\d)")
_ITEM = re.compile(r"^PEP\s*(\d+)\s*(?:§\s*(.*?))?\s*$")


@dataclass(frozen=True)
class Citation:
    pep: int
    section: str | None
    raw: str  # the full bracket as it appeared in the answer, e.g. "[PEP 572 §Rationale]"
    start: int
    end: int


def _normalise(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


def parse_citations(answer: str) -> list[Citation]:
    """Find every ``[PEP N §Section]`` citation in the answer, in order.

    A bracket may hold several sources separated by ``;`` or ``, PEP``, e.g.
    ``[PEP 572 §Rationale; PEP 8 §Naming]``; each becomes its own Citation sharing
    the bracket's raw text and span.
    """
    found: list[Citation] = []
    for bracket in _BRACKET.finditer(answer):
        for item in _ITEM_SPLIT.split(bracket.group(1)):
            match = _ITEM.match(item.strip())
            if not match:
                continue
            section = match.group(2) or None
            found.append(
                Citation(
                    pep=int(match.group(1)),
                    section=section,
                    raw=bracket.group(0),
                    start=bracket.start(),
                    end=bracket.end(),
                )
            )
    return found


@dataclass
class ResolvedCitations:
    cited: list[Any]  # passages cited by the answer, in order of first citation
    dropped: list[str]  # raw bracket texts whose PEP is not in the retrieved sources
    answer: str  # answer text with dropped citations removed


def _match_passage(citation: Citation, passages: Sequence[Any]) -> int | None:
    same_pep = [i for i, p in enumerate(passages) if p.pep == citation.pep]
    if not same_pep:
        return None
    if citation.section is None:
        return same_pep[0]
    wanted = _normalise(citation.section)
    for i in same_pep:  # exact section match first
        if _normalise(passages[i].section) == wanted:
            return i
    for i in same_pep:  # then a loose match, e.g. "Rationale" vs "Rationale and Motivation"
        have = _normalise(passages[i].section)
        if have and (wanted in have or have in wanted):
            return i
    return same_pep[0]  # right PEP, unknown section: keep the best-ranked passage of that PEP


def resolve_citations(answer: str, passages: Sequence[Any]) -> ResolvedCitations:
    """Map citations to retrieved passages and strip any that name an unretrieved PEP."""
    cited_index: list[int] = []
    dropped: list[str] = []
    text_pieces: list[str] = []
    last = 0
    retrieved_peps = {p.pep for p in passages}
    for citation in parse_citations(answer):
        if citation.start < last:
            continue  # second item of a bracket we already handled
        bracket_items = parse_citations(citation.raw)
        keep = [c for c in bracket_items if c.pep in retrieved_peps]
        if len(keep) == len(bracket_items):
            for c in keep:
                index = _match_passage(c, passages)
                if index is not None and index not in cited_index:
                    cited_index.append(index)
        else:
            dropped.append(citation.raw)
        text_pieces.append(answer[last:citation.start])
        if keep:
            # keep only the valid items of a partly-valid bracket
            text_pieces.append("[" + "; ".join(_render(c) for c in keep) + "]")
        last = citation.end
    text_pieces.append(answer[last:])
    cleaned = re.sub(r"[ \t]+([.,;:)])", r"\1", "".join(text_pieces))
    cleaned = re.sub(r"  +", " ", cleaned).strip()
    return ResolvedCitations(cited=[passages[i] for i in cited_index], dropped=dropped, answer=cleaned)


def _render(citation: Citation) -> str:
    return f"PEP {citation.pep}" + (f" §{citation.section}" if citation.section else "")


# ---------------------------------------------------------------- ask


@dataclass
class AskResult:
    question: str
    mode: str
    answer: str
    grounded: bool  # True when the answer cites at least one retrieved passage
    cited: list[Any]  # retrieved passages the answer cites
    passages: list[Any]  # everything given to the model, numbered in prompt order
    dropped_citations: list[str] = field(default_factory=list)
    model: str = ""


Retrieve = Callable[[str, str, int], Sequence[Any]]  # (question, mode, k) -> passages


def answer_question(
    question: str,
    passages: Sequence[Any],
    llm: LLM,
    *,
    mode: str = "hybrid",
    model: str = "",
) -> AskResult:
    """Prompt the model with already-retrieved passages and resolve its citations."""
    system, user = build_prompt(question, passages)
    raw = llm.complete(system, user).strip()
    resolved = resolve_citations(raw, passages)
    return AskResult(
        question=question,
        mode=mode,
        answer=resolved.answer,
        grounded=bool(resolved.cited),
        cited=resolved.cited,
        passages=list(passages),
        dropped_citations=resolved.dropped,
        model=model,
    )


def ask(question: str, retrieve: Retrieve, llm: LLM, *, mode: str = "hybrid", k: int = 5, model: str = "") -> AskResult:
    """Retrieve the top ``k`` passages for ``question`` in ``mode``, then answer."""
    question = question.strip()
    if not question:
        raise ValueError("question must not be empty")
    passages = list(retrieve(question, mode, k))
    return answer_question(question, passages, llm, mode=mode, model=model)
