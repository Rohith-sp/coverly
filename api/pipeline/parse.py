"""Study plan text -> topic tree (PRD §6 F3). One LLM call on the large model."""

import hashlib
import logging
import re

import httpx

import llm
from config import get_settings
from schemas import MAX_TOPICS, ModuleOut, ParsedPlan, TopicOut

log = logging.getLogger(__name__)

SLUG_MAX_CHARS = 40

SYSTEM_PROMPT = """You turn a study plan or syllabus into a topic tree for finding videos.
Return JSON only, exactly this shape:
{"title": "<short plan title>",
 "modules": [{"name": "<module name>", "topics": ["<topic>", "<topic>"]}]}
Rules:
- Keep the syllabus order of modules and topics.
- A topic is one concrete, searchable concept (2-6 words), not a sentence.
- Do not invent topics that are not in the plan. Do not repeat a topic.
- Group topics under the plan's own sections; if it has none, make sensible modules.
- At most 40 topics in total."""


class TooManyTopicsError(Exception):
    """The parsed plan has more topics than the v0 cap; the user must trim or split it."""

    def __init__(self, count: int, limit: int) -> None:
        super().__init__(f"{count} topics; the limit is {limit}. Trim or split the plan.")
        self.count = count
        self.limit = limit


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:SLUG_MAX_CHARS].rstrip("-") or "topic"


def topic_id(module: str, name: str) -> str:
    """Stable id: slug(name) + "-" + first 4 hex chars of sha1("module|name")."""
    digest = hashlib.sha1(f"{module}|{name}".encode(), usedforsecurity=False).hexdigest()
    return f"{_slug(name)}-{digest[:4]}"


def build_modules(parsed: ParsedPlan) -> list[ModuleOut]:
    """Assign ids, drop duplicate topics (same id, or same name within a module)."""
    seen_ids: set[str] = set()
    modules: list[ModuleOut] = []
    for module in parsed.modules:
        seen_names: set[str] = set()
        topics: list[TopicOut] = []
        for name in module.topics:
            tid = topic_id(module.name, name)
            key = name.casefold()
            if key in seen_names or tid in seen_ids:
                continue
            seen_names.add(key)
            seen_ids.add(tid)
            topics.append(TopicOut(topic_id=tid, name=name))
        if topics:
            modules.append(ModuleOut(name=module.name, topics=topics))
    return modules


async def parse_plan(client: httpx.AsyncClient, text: str) -> tuple[str, list[ModuleOut], int]:
    """Return (title, modules with stable topic ids, LLM tokens used).

    Raises `TooManyTopicsError` above the cap, and `llm.LLMError` subclasses on LLM failure.
    """
    parsed, tokens = await llm.complete_json(client, "large", SYSTEM_PROMPT, text, ParsedPlan)
    modules = build_modules(parsed)
    count = sum(len(m.topics) for m in modules)
    limit = min(MAX_TOPICS, get_settings().max_topics)
    log.info("parse plan modules=%d topics=%d tokens=%d", len(modules), count, tokens)
    if count > limit:
        raise TooManyTopicsError(count, limit)
    return parsed.title, modules, tokens
