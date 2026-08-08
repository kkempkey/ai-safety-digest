"""Curate stage: Opus 5 editorial pass — summarize items, then frame the edition.

Pass A (chunked): per-item include/theme/headline/summary/why-it-matters/significance.
Pass B (single):  edition title, intro, top story selection.
"""

import json
from typing import List, Optional

from .llm import backend, structured_call

CURATE_MODEL = "claude-opus-5"
# The CLI backend is much slower per call than the API, so keep its chunks
# small enough that one chunk can't run away.
CHUNK_SIZE = 20 if backend() == "api" else 8

# The editorial rubric IS the product. Keep it byte-identical across calls
# (it is a cached prefix) — never interpolate the date or anything volatile here.
EDITORIAL_RUBRIC = """You are the editor of a daily AI safety digest read by one well-informed person who follows the field closely. Your job: decide what belongs in today's edition, and write tight, accurate entries for what does.

RELEVANCE — include only items with a genuine AI-safety angle:
- Research: alignment, interpretability, evaluations, red-teaming, control, model behavior, dangerous-capability studies. A generic ML paper does not qualify unless it bears on safety, evals, or governance.
- Policy: regulation, AI Acts, safety institutes, compute governance, export controls, standards, liability, significant think-tank output.
- Industry: frontier lab releases and safety frameworks, safety-relevant incidents, deployment decisions, notable commitments or reversals.
- Community: substantive essays, debates, forecasts, and analysis from the AI safety community. Substance over takes: a post that advances an argument qualifies; a link roundup usually does not.
Set include=false for anything merely AI-adjacent (products, funding, stock moves, tutorials, culture-war content). Be strict — a shorter digest of real items beats a padded one.

THEMES: research | policy | industry | community. Choose by what the item IS, not who published it. A DeepMind interpretability paper is research, not industry. A LessWrong post analyzing the EU AI Act is policy.

HEADLINE: rewrite for clarity in under 12 words. Plain, specific, no clickbait, no source name in the headline.

SUMMARY: 1-2 sentences stating what actually happened or what is actually claimed. Concrete: name the actors, the method, the number. Never editorialize here.

WHY_IT_MATTERS: exactly one sentence of consequence — what this changes, enables, threatens, or settles. It must add information beyond the summary; if you find yourself restating the summary, you have not found the significance, so think harder or score it lower.

SIGNIFICANCE (1-5), anchored:
- 1: routine — an incremental paper, a minor org update, a standard weekly roundup.
- 3: notable — a solid new research result, a meaningful policy development, a frontier lab safety announcement worth reading about. Most included items land at 2-3.
- 5: field-shaping — a major capability or alignment breakthrough, binding regulation passing, a serious safety incident at a frontier lab, a landmark eval result. Rare; most days have none.
Prestige of source must not inflate significance. An arXiv preprint that changes how people think about deception scores higher than a routine announcement from a famous lab.

Some items are podcast episodes described only by title and show notes: judge them on the substance the notes describe, and say "discusses" rather than asserting the claims yourself.

Respond for every id you were given."""

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "include": {"type": "boolean"},
                    "theme": {
                        "type": "string",
                        "enum": ["research", "policy", "industry", "community"],
                    },
                    "headline": {"type": "string"},
                    "summary": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "significance": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                },
                "required": ["id", "include", "theme", "headline",
                             "summary", "why_it_matters", "significance"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

FRAMING_SYSTEM = """You write the front matter for a daily AI safety digest. Given today's included items (headline, theme, significance), produce:
- edition_title: a specific title for today under 10 words, drawn from the day's actual content — never generic like "AI Safety News".
- intro: 2-3 sentences framing the day. Name the dominant thread if there is one; if the day is quiet or scattered, say so plainly rather than manufacturing a narrative.
- top_story_ids: 3-5 item ids, most important first. Choose by significance and breadth of consequence, not by theme balance."""

FRAMING_SCHEMA = {
    "type": "object",
    "properties": {
        "edition_title": {"type": "string"},
        "intro": {"type": "string"},
        "top_story_ids": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["edition_title", "intro", "top_story_ids"],
    "additionalProperties": False,
}


def curate_items(candidates: List[dict], date_label: str, log=print) -> List[dict]:
    """Pass A. Returns curated entries joined back to their source rows."""
    indexed = {i: c for i, c in enumerate(candidates)}
    curated: List[dict] = []

    for start in range(0, len(candidates), CHUNK_SIZE):
        chunk_ids = list(range(start, min(start + CHUNK_SIZE, len(candidates))))
        payload = json.dumps({
            "date": date_label,
            "items": [
                {
                    "id": i,
                    "title": indexed[i]["title"],
                    "source": indexed[i]["source"],
                    "theme_hint": indexed[i].get("theme_hint"),
                    "snippet": (indexed[i]["snippet"] or "")[:600],
                }
                for i in chunk_ids
            ],
        })
        try:
            result = structured_call(CURATE_MODEL, EDITORIAL_RUBRIC,
                                     payload, ITEM_SCHEMA, log=log)
        except Exception as exc:  # retry the chunk once; then skip it, not the run
            log("  WARN curate chunk %d failed (%s), retrying once" % (start, exc))
            result = structured_call(CURATE_MODEL, EDITORIAL_RUBRIC,
                                     payload, ITEM_SCHEMA, log=log)
        for entry in result["items"]:
            src = indexed.get(entry["id"])
            if src is None or not entry["include"]:
                continue
            curated.append({
                "url_hash": src["url_hash"],
                "url": src["url"],
                "source": src["source"],
                "published": src.get("published") or src.get("first_seen"),
                "theme": entry["theme"],
                "headline": entry["headline"],
                "summary": entry["summary"],
                "why_it_matters": entry["why_it_matters"],
                "significance": entry["significance"],
            })
        log("  curate: chunk %d-%d → %d included so far"
            % (start, chunk_ids[-1], len(curated)))
    return curated


def frame_edition(curated: List[dict], date_label: str, log=print) -> dict:
    """Pass B. Falls back to significance ranking with no intro on failure."""
    payload = json.dumps({
        "date": date_label,
        "items": [
            {"id": i, "headline": c["headline"], "theme": c["theme"],
             "significance": c["significance"]}
            for i, c in enumerate(curated)
        ],
    })
    try:
        result = structured_call(CURATE_MODEL, FRAMING_SYSTEM,
                                 payload, FRAMING_SCHEMA, max_tokens=4000, log=log)
        top_ids = [i for i in result["top_story_ids"] if 0 <= i < len(curated)][:5]
        if not top_ids:
            raise ValueError("empty top_story_ids")
        return {
            "edition_title": result["edition_title"],
            "intro": result["intro"],
            "top_story_ids": top_ids,
        }
    except Exception as exc:
        log("  WARN framing failed (%s); falling back to significance ranking" % exc)
        ranked = sorted(range(len(curated)),
                        key=lambda i: -curated[i]["significance"])[:4]
        return {"edition_title": "AI Safety Digest", "intro": "", "top_story_ids": ranked}


def curate(candidates: List[dict], date_label: str, log=print) -> dict:
    """Full curation: returns the edition payload consumed by render/email."""
    items = curate_items(candidates, date_label, log)
    if not items:
        return {"edition_title": "AI Safety Digest", "intro": "",
                "top_story_ids": [], "items": []}
    framing = frame_edition(items, date_label, log)
    return {**framing, "items": items}
