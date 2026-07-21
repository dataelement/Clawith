"""Experience library — AI consumption side (PRD v2 P0-4, hybrid pull).

Nothing heavy sits in the agent's context: only a one-line hint. The agent then
pulls on demand:
    search_experience(keyword) → lightweight candidates (title + applicability)
    read_experience(entry_id)  → full text, records a `read`

Adoption is recorded separately: when the agent's final output cites an entry
with a [[exp:<uuid>]] marker, `record_experience_citations` logs a `cited` row.
Read != used — the kill-switch metric counts `cited` only.

All reads honor P0-6 visibility and never surface legacy_plaza imports.
"""

import math
import re
import uuid
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import and_, exists, or_, select

from app.database import async_session
from app.models.agent import Agent
from app.models.experience import ExperienceEntry
from app.models.experience_reference import ExperienceReference
from app.models.llm import LLMModel
from app.models.org import OrgMember
from app.models.system_settings import SystemSetting
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome

# Agents echo this marker in their final answer to cite an entry they actually used.
CITATION_RE = re.compile(r"\[\[exp:([0-9a-fA-F-]{36})\]\]")

# ── Query expansion (① synonym expansion; toggle via system setting) ──
_QUERY_EXPANSION_SETTING = "experience_query_expansion"  # value {"enabled": bool}, default on
_EXPANSION_CACHE: dict[str, list[str]] = {}
_EXPANSION_CACHE_CAP = 512
_EXPANSION_SYS_PROMPT = (
    "你是检索同义词扩展器。为给定检索词生成 5-8 个语义相同或高度相近的中文近义表达/同义词，"
    "用于在企业内部经验库做关键词匹配。严格要求：只给近义或同义词，不要扩展到相关但不同的概念，"
    "不要发散，不要解释，只输出用逗号分隔的词。检索词："
)

_HINT = (
    "\n## Team Experience Library\n"
    "Your team keeps a private, human-curated library of hard-won internal experience "
    "(internal-system gotchas, private-deployment config, hidden process rules) that public web "
    "search cannot surface. When your current work touches internal systems, internal processes, "
    "or a private/self-hosted environment, FIRST call `search_experience` with a few keywords. "
    "If a candidate's applicability matches your situation, call `read_experience` to read it in full "
    "and follow it. When an entry actually informs your answer, do BOTH: (1) state it in plain language "
    "to the user — e.g. begin the relevant part with 「本次参考了团队经验库」and name what you drew on; "
    "and (2) append the marker `[[exp:<entry_id>]]` (id from the search/read results) right there — the "
    "marker is the machine record of adoption, the sentence is for the human. "
    "If nothing matches, ignore this and do not invent experiences.\n"
    "你无权写入团队经验库。当用户要求你把某条经验『记成经验 / 沉淀』时，"
    "不要写进 memory 或 workspace，而是调用 `propose_experience_draft`，整理出标题、"
    "markdown 正文，以及必填的『适用条件与失效信号』，"
    "并如实回执：例如「我不能直接帮你记成经验，但我已把相关内容整理成结构化草稿，"
    "点击下方『沉淀为经验』确认后即可入库」。"
)

_MAX_CANDIDATES = 8
_SEARCH_POOL_CAP = 500  # visible-published rows scored in Python per search; log if exceeded


def _token_needles(token: str) -> list[str]:
    """Substrings that count as a match for one query token.

    For CJK compounds longer than 2 chars (which whitespace tokenization can't split),
    also accept any adjacent 2-char slice — a lightweight stand-in for segmentation so
    "合同条款" matches text containing "合同" or "条款".
    """
    if len(token) > 2 and any("一" <= c <= "鿿" for c in token):
        return [token] + [token[i:i + 2] for i in range(len(token) - 1)]
    return [token]


async def _resolve_agent(db, agent_id: uuid.UUID) -> Agent | None:
    return (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()


async def _agent_department_ids(db, agent: Agent) -> set[uuid.UUID]:
    """Departments an agent belongs to = the department(s) of its creator.

    Agents have no first-class department; they inherit it from the human who
    created them (agent.creator_id → OrgMember.user_id → OrgMember.department_id).
    """
    if not agent.creator_id:
        return set()
    rows = await db.execute(
        select(OrgMember.department_id).where(
            OrgMember.user_id == agent.creator_id,
            OrgMember.tenant_id == agent.tenant_id,
            OrgMember.department_id.isnot(None),
        )
    )
    return {r[0] for r in rows.all() if r[0]}


def _visibility_condition(dept_ids: set[uuid.UUID]):
    """P0-6 filter for an agent consumer: company always; department if matched.

    user-scoped entries are for human viewing in the UI and are never surfaced to
    agents. If the org hierarchy is empty, dept_ids is empty and only company shows
    (the degrade rule holds naturally).
    """
    conds = [ExperienceEntry.visibility_scope == "company"]
    if dept_ids:
        conds.append(
            and_(
                ExperienceEntry.visibility_scope == "department",
                ExperienceEntry.visibility_scope_id.in_(dept_ids),
            )
        )
    return or_(*conds)


def _freshness_marker(entry: ExperienceEntry) -> str:
    # P1-2: stale entries are downweighted; flag them so the agent trusts them less.
    if not entry.last_reviewed_at:
        return "⚠️ 未复核"
    age = datetime.now(timezone.utc) - entry.last_reviewed_at
    return "⚠️ 复核超期" if age > timedelta(days=90) else "✅"


async def build_experience_hint(agent_id: uuid.UUID) -> str:
    """Return the always-on hint, or "" when the tenant has no published entries.

    Cold-start / empty library → no hint, so we never nudge the agent to search
    a library that has nothing in it.
    """
    try:
        async with async_session() as db:
            agent = await _resolve_agent(db, agent_id)
            if not agent or agent.is_system:
                return ""
            has_any = (
                await db.execute(
                    select(
                        exists().where(
                            and_(
                                ExperienceEntry.tenant_id == agent.tenant_id,
                                ExperienceEntry.status == "published",
                                ExperienceEntry.origin != "legacy_plaza",
                            )
                        )
                    )
                )
            ).scalar()
            if not has_any:
                return ""
            hint = _HINT
            # ④ Existing tag vocabulary — nudge the agent to reuse tags instead of coining near-duplicates.
            tag_rows = await db.execute(
                select(ExperienceEntry.tags).where(
                    ExperienceEntry.tenant_id == agent.tenant_id,
                    ExperienceEntry.status != "retired",
                )
            )
            counts: dict[str, int] = {}
            for (tags,) in tag_rows.all():
                for tg in (tags or []):
                    tg = str(tg).strip()
                    if tg:
                        counts[tg] = counts.get(tg, 0) + 1
            if counts:
                top = [tg for tg, _ in sorted(counts.items(), key=lambda x: -x[1])[:40]]
                hint += (
                    "\n沉淀经验时，标签优先从下列现有标签中复用；语义相同就用既有的，不要新造近义标签："
                    + " / ".join(top)
                )
            return hint
    except Exception as e:
        logger.warning(f"build_experience_hint failed for {agent_id}: {e}")
        return ""


async def _query_expansion_enabled(db) -> bool:
    """Read the on/off toggle (default enabled if the setting is absent)."""
    try:
        row = (await db.execute(select(SystemSetting).where(SystemSetting.key == _QUERY_EXPANSION_SETTING))).scalar_one_or_none()
        if row and isinstance(row.value, dict) and "enabled" in row.value:
            return bool(row.value["enabled"])
    except Exception:
        pass
    return True


async def _expand_query(db, agent, keyword: str) -> list[str]:
    """Return 5-8 strict synonyms/near-expressions for the keyword (cached, best-effort).

    Uses the agent's own model, low tokens, temperature 0. Any failure → []."""
    key = keyword.lower().strip()
    if key in _EXPANSION_CACHE:
        return _EXPANSION_CACHE[key]
    terms: list[str] = []
    try:
        model_id = agent.primary_model_id or agent.fallback_model_id
        model = (await db.execute(select(LLMModel).where(LLMModel.id == model_id))).scalar_one_or_none() if model_id else None
        if model:
            from app.services.llm import get_model_api_key
            from app.services.llm.client import chat_complete
            resp = await chat_complete(
                provider=model.provider, api_key=get_model_api_key(model), model=model.model, base_url=model.base_url,
                messages=[{"role": "system", "content": _EXPANSION_SYS_PROMPT + keyword}],
                temperature=0.0, max_tokens=120,
            )
            text = resp["choices"][0]["message"].get("content") or ""
            seen = set()
            for t in re.split(r"[,，、\n]+", text):
                t = t.strip().strip("·-•").strip()
                if t and t.lower() not in seen and len(t) <= 20:
                    seen.add(t.lower())
                    terms.append(t)
            terms = terms[:8]
    except Exception as e:
        logger.warning(f"query expansion failed for “{keyword}”: {e}")
        terms = []
    if len(_EXPANSION_CACHE) > _EXPANSION_CACHE_CAP:
        _EXPANSION_CACHE.clear()
    _EXPANSION_CACHE[key] = terms
    return terms


async def search_experience_outcome(
    agent_id: uuid.UUID,
    arguments: dict,
) -> ToolExecutionOutcome:
    """Return a typed search fact over the visible, published library."""
    keyword = arguments.get("keyword") or arguments.get("query") or ""
    if not isinstance(keyword, str):
        return ToolExecutionOutcome(
            status="failed",
            result_summary="search_experience keyword must be a string.",
            result_ref=None,
            error_code="invalid_tool_arguments",
        )
    keyword = keyword.strip()
    # Tokenize on whitespace: agents pass multi-word queries (e.g. "合同 验收 合格"),
    # which must match per-term, not as one contiguous substring. Dedup, keep order.
    tokens = list(dict.fromkeys(tok for tok in keyword.lower().split() if tok))[:24]
    if not tokens:
        return ToolExecutionOutcome(
            status="failed",
            result_summary="search_experience requires keyword.",
            result_ref=None,
            error_code="invalid_tool_arguments",
        )
    try:
        async with async_session() as db:
            agent = await _resolve_agent(db, agent_id)
            if not agent or agent.is_system:
                return ToolExecutionOutcome(
                    status="failed",
                    result_summary="This Agent cannot access the experience library.",
                    result_ref=None,
                    error_code="experience_access_denied",
                )
            dept_ids = await _agent_department_ids(db, agent)

            # ① Query expansion: fold in strict synonyms so differently-phrased entries still match.
            if await _query_expansion_enabled(db):
                for term in await _expand_query(db, agent, keyword):
                    tl = term.lower().strip()
                    if tl and tl not in tokens:
                        tokens.append(tl)
                tokens = tokens[:24]

            # Candidate pool: entries visible to this agent (published, non-legacy).
            # Tokenized scoring across title + body + applicability + JSON tags is done in
            # Python — tags aren't portably matchable in SQL, and per-token scoring drives
            # ranking. For a curated private library this pool is small.
            pool_q = (
                select(ExperienceEntry)
                .where(
                    ExperienceEntry.tenant_id == agent.tenant_id,
                    ExperienceEntry.status == "published",
                    ExperienceEntry.origin != "legacy_plaza",
                    _visibility_condition(dept_ids),
                )
                .order_by(ExperienceEntry.last_reviewed_at.desc())
                .limit(_SEARCH_POOL_CAP + 1)
            )
            pool = (await db.execute(pool_q)).scalars().all()
            if len(pool) > _SEARCH_POOL_CAP:
                logger.warning(
                    f"search_experience: visible pool exceeds {_SEARCH_POOL_CAP} for agent {agent_id}; "
                    "ranking over the most-recently-reviewed subset only (evolve to tag/keyword prefilter)."
                )
                pool = pool[:_SEARCH_POOL_CAP]

            # Score each entry by how many query tokens appear across title + 正文 + 适用条件 + tags.
            # A CJK compound token (e.g. "合同条款") that doesn't appear verbatim also matches on any of
            # its 2-char slices ("合同"/"条款") — approximates Chinese segmentation without a tokenizer.
            token_needles = [_token_needles(tok) for tok in tokens]
            # First pass: which query tokens each entry matches, and each token's document frequency.
            hits: list[tuple[ExperienceEntry, set[int]]] = []
            df = [0] * len(tokens)
            for e in pool:
                blob = " ".join(
                    filter(None, [
                        e.title, e.body, e.applicability,
                        " ".join(str(t) for t in (e.tags or [])),
                    ])
                ).lower()
                matched = {ti for ti, needles in enumerate(token_needles) if any(n in blob for n in needles)}
                if matched:
                    for ti in matched:
                        df[ti] += 1
                    hits.append((e, matched))

            if not hits:
                return ToolExecutionOutcome(
                    status="succeeded",
                    result_summary=(
                        f"No experience entries match “{keyword}”. "
                        "Proceed without internal experience."
                    ),
                    result_ref=None,
                )

            # Score = sum of matched tokens' IDF. Rarer tokens weigh more; smoothed so any
            # match always scores > 0 (log((N+1)/df) stays positive even when df == N).
            n_docs = len(pool)
            idf = [math.log((n_docs + 1) / d) if d else 0.0 for d in df]
            _floor = datetime.min.replace(tzinfo=timezone.utc)
            scored = [(sum(idf[ti] for ti in matched), e) for e, matched in hits]
            scored.sort(key=lambda se: (se[0], se[1].last_reviewed_at or _floor), reverse=True)
            entries = [e for _, e in scored[:_MAX_CANDIDATES]]

            lines = [
                f"Found {len(entries)} candidate experience entr(y/ies) for “{keyword}”. "
                "Read the full entry only if its applicability matches your situation:\n"
            ]
            for e in entries:
                applic = (e.applicability or "").strip().replace("\n", " ")
                if len(applic) > 160:
                    applic = applic[:160] + "…"
                lines.append(
                    f"- {_freshness_marker(e)} **{e.title or '(untitled)'}** "
                    f"[[exp:{e.id}]]\n  适用条件/失效信号: {applic}"
                )
            lines.append("\nTo read one: call `read_experience` with its entry id.")
            return ToolExecutionOutcome(
                status="succeeded",
                result_summary="\n".join(lines),
                result_ref=None,
            )
    except Exception as e:
        logger.warning(f"search_experience failed for {agent_id}: {e}")
        return ToolExecutionOutcome(
            status="failed",
            result_summary=f"Experience search failed: {type(e).__name__}.",
            result_ref=None,
            error_code="experience_search_failed",
            retryable=True,
        )


async def search_experience(agent_id: uuid.UUID, arguments: dict) -> str:
    """Legacy display adapter for the typed experience search."""
    outcome = await search_experience_outcome(agent_id, arguments)
    return outcome.result_summary or "Experience search returned no summary."


async def read_experience_outcome(
    agent_id: uuid.UUID,
    arguments: dict,
) -> ToolExecutionOutcome:
    """Return one visible entry; read telemetry cannot change the read fact."""
    raw_id = str(arguments.get("entry_id") or "").strip()
    # Tolerate the agent pasting the full "[[exp:<uuid>]]" citation marker.
    m = re.search(r"[0-9a-fA-F-]{36}", raw_id)
    try:
        entry_id = uuid.UUID(m.group(0) if m else raw_id)
    except (ValueError, AttributeError):
        return ToolExecutionOutcome(
            status="failed",
            result_summary="read_experience requires a valid entry_id.",
            result_ref=None,
            error_code="invalid_tool_arguments",
        )
    try:
        async with async_session() as db:
            agent = await _resolve_agent(db, agent_id)
            if not agent or agent.is_system:
                return ToolExecutionOutcome(
                    status="failed",
                    result_summary="This Agent cannot access the experience library.",
                    result_ref=None,
                    error_code="experience_access_denied",
                )
            dept_ids = await _agent_department_ids(db, agent)
            entry = (
                await db.execute(
                    select(ExperienceEntry).where(
                        ExperienceEntry.id == entry_id,
                        ExperienceEntry.tenant_id == agent.tenant_id,
                        ExperienceEntry.status == "published",
                        ExperienceEntry.origin != "legacy_plaza",
                        _visibility_condition(dept_ids),
                    )
                )
            ).scalar_one_or_none()
            if not entry:
                return ToolExecutionOutcome(
                    status="failed",
                    result_summary="Experience entry not found or not visible.",
                    result_ref=None,
                    error_code="experience_not_found",
                )

            tags = ", ".join(entry.tags or []) or "—"
            summary = (
                f"📚 Experience [[exp:{entry.id}]] — {entry.title}\n"
                f"标签: {tags} · 复核: {_freshness_marker(entry)}\n\n"
                f"{entry.body}\n\n"
                f"## 适用条件与失效信号\n{entry.applicability}\n\n"
                f"If this informs your answer: (1) tell the user in plain language you referenced the "
                f"team experience library and what you took from it, and (2) append [[exp:{entry.id}]] "
                "right there as the adoption record. "
                "If your situation no longer matches the applicability above, do not apply it."
            )
            try:
                db.add(
                    ExperienceReference(
                        entry_id=entry.id,
                        kind="read",
                        tenant_id=agent.tenant_id,
                        agent_id=agent.id,
                    )
                )
                await db.commit()
            except Exception as telemetry_error:
                logger.warning(
                    "Experience read succeeded but telemetry failed for %s: %s",
                    entry.id,
                    type(telemetry_error).__name__,
                )
            return ToolExecutionOutcome(
                status="succeeded",
                result_summary=summary,
                result_ref=None,
            )
    except Exception as e:
        logger.warning(f"read_experience failed for {agent_id}/{raw_id}: {e}")
        return ToolExecutionOutcome(
            status="failed",
            result_summary=f"Failed to read experience: {type(e).__name__}.",
            result_ref=None,
            error_code="experience_read_failed",
            retryable=True,
        )


async def read_experience(agent_id: uuid.UUID, arguments: dict) -> str:
    """Legacy display adapter for the typed experience read."""
    outcome = await read_experience_outcome(agent_id, arguments)
    return outcome.result_summary or "Experience read returned no summary."


async def record_experience_citations(
    text: str,
    agent_id: uuid.UUID,
    session_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
) -> int:
    """Scan a final agent output for [[exp:<uuid>]] markers and log `cited` references.

    Best-effort: only records citations for entries that are published and visible
    to the agent (guards against hallucinated / stale ids). Deduplicated per entry.
    Returns the number of citations recorded.
    """
    if not text:
        return 0
    ids: set[uuid.UUID] = set()
    for m in CITATION_RE.findall(text):
        try:
            ids.add(uuid.UUID(m))
        except ValueError:
            continue
    if not ids:
        return 0
    try:
        async with async_session() as db:
            agent = await _resolve_agent(db, agent_id)
            if not agent:
                return 0
            dept_ids = await _agent_department_ids(db, agent)
            valid = (
                await db.execute(
                    select(ExperienceEntry.id).where(
                        ExperienceEntry.id.in_(ids),
                        ExperienceEntry.tenant_id == agent.tenant_id,
                        ExperienceEntry.status == "published",
                        ExperienceEntry.origin != "legacy_plaza",
                        _visibility_condition(dept_ids),
                    )
                )
            ).scalars().all()
            existing: set[uuid.UUID] = set()
            if valid and message_id is not None:
                existing = set(
                    (
                        await db.execute(
                            select(ExperienceReference.entry_id).where(
                                ExperienceReference.entry_id.in_(valid),
                                ExperienceReference.kind == "cited",
                                ExperienceReference.message_id == message_id,
                            )
                        )
                    ).scalars().all()
                )
            new_citations = [eid for eid in valid if eid not in existing]
            for eid in new_citations:
                db.add(
                    ExperienceReference(
                        entry_id=eid,
                        kind="cited",
                        tenant_id=agent.tenant_id,
                        agent_id=agent.id,
                        session_id=session_id,
                        message_id=message_id,
                    )
                )
            if new_citations:
                await db.commit()
            return len(new_citations)
    except Exception as e:
        logger.warning(f"record_experience_citations failed for {agent_id}: {e}")
        return 0
