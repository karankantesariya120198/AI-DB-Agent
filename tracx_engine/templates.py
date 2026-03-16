"""
Query templates loaded from YAML domain configuration.

Templates map intent names to parameterized SQL queries so the QueryEngine
can skip the multi-step LLM agent pipeline for known question patterns.
"""

import os
import re
from dataclasses import dataclass
from typing import Dict, Any, Set

import yaml

from helper import get_logger, load_yaml_config

logger = get_logger("query_templates")


@dataclass
class QueryTemplate:
    intent: str
    description: str
    sql: str
    params: Dict[str, Dict[str, Any]]
    response_hint: str


# Module-level state — populated by load_templates() / configure()
TEMPLATES: Dict[str, QueryTemplate] = {}
DETAIL_INTENTS: Set[str] = set()

_FRAGMENT_RE = re.compile(r"\$\{(\w+)\}")


def _resolve_fragments(sql: str, fragments: Dict[str, str]) -> str:
    """Replace ${name} tokens in *sql* with their fragment values."""
    def _replacer(match):
        name = match.group(1)
        if name not in fragments:
            raise KeyError(f"Unknown SQL fragment: ${{{name}}}")
        return fragments[name].strip()
    return _FRAGMENT_RE.sub(_replacer, sql)


def load_templates(config_path: str = None) -> tuple:
    """Load query templates from a YAML config file.

    Returns (templates_dict, detail_intents_set).
    Falls back to empty collections if the file is missing.
    """
    path = config_path or os.getenv("AGENT_CONFIG", "config.yaml")

    try:
        config = load_yaml_config(path)
    except (FileNotFoundError, yaml.YAMLError) as exc:
        logger.warning("Could not load templates from %s: %s", path, exc)
        return {}, set()

    if not config:
        return {}, set()

    fragments = config.get("sql_fragments", {}) or {}
    raw_templates = config.get("query_templates", {}) or {}
    detail_list = config.get("detail_intents", []) or []

    templates: Dict[str, QueryTemplate] = {}
    for intent, tdef in raw_templates.items():
        sql = _resolve_fragments(tdef["sql"], fragments)
        templates[intent] = QueryTemplate(
            intent=intent,
            description=tdef.get("description", ""),
            sql=sql,
            params=tdef.get("params", {}) or {},
            response_hint=tdef.get("response_hint", ""),
        )

    detail_intents = set(detail_list)

    logger.info(
        "Loaded %d query templates (%d detail intents) from %s",
        len(templates), len(detail_intents), path,
    )
    return templates, detail_intents


def configure(config_path: str = None):
    """(Re-)load templates from *config_path*, updating module globals."""
    global TEMPLATES, DETAIL_INTENTS
    TEMPLATES, DETAIL_INTENTS = load_templates(config_path)


# Module-level initialisation — attempt default path on import
TEMPLATES, DETAIL_INTENTS = load_templates()
