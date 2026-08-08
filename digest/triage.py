"""Triage stage: Haiku 4.5 binary relevance pass over tier-2 candidates.

Tier-1 items skip this stage entirely. On any failure the caller falls back to
passing top prefiltered items straight to curation.
"""

import json
from typing import List

from .llm import structured_call

TRIAGE_MODEL = "claude-haiku-4-5"
CHUNK_SIZE = 40

TRIAGE_SYSTEM = """You are the triage filter for a daily AI safety digest.

You receive a JSON list of candidate items (id, title, source, snippet). For each, decide keep or drop.

KEEP items that are genuinely relevant to AI safety, broadly construed:
- alignment, interpretability, evaluations, red-teaming, model behavior research
- AI governance, regulation, policy, safety institutes, compute governance
- frontier lab announcements with safety relevance (new models, safety frameworks, incidents)
- catastrophic/existential risk discussion, AI control, misuse (cyber, bio)
- serious commentary or analysis on any of the above

DROP items that are merely AI-adjacent:
- product reviews, gadget news, funding rounds without safety relevance
- generic ML papers with no safety/alignment/eval angle
- business/stock coverage, personnel moves without policy significance
- tutorials, how-tos, consumer AI tips

When genuinely uncertain, KEEP — a later editorial pass applies stricter judgment.
Return a decision for every id you were given."""

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "keep": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "keep", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["decisions"],
    "additionalProperties": False,
}


def triage(candidates: List[dict], log=print) -> List[dict]:
    """Return the kept subset of candidates. Tier-1 items pass through untouched."""
    tier1 = [c for c in candidates if c["tier"] == 1]
    tier2 = [c for c in candidates if c["tier"] >= 2]
    if not tier2:
        return tier1

    indexed = {i: c for i, c in enumerate(tier2)}
    kept_ids = set()

    for start in range(0, len(tier2), CHUNK_SIZE):
        chunk_ids = list(range(start, min(start + CHUNK_SIZE, len(tier2))))
        payload = [
            {
                "id": i,
                "title": indexed[i]["title"],
                "source": indexed[i]["source"],
                "snippet": (indexed[i]["snippet"] or "")[:300],
            }
            for i in chunk_ids
        ]
        result = structured_call(TRIAGE_MODEL, TRIAGE_SYSTEM, json.dumps(payload),
                                 TRIAGE_SCHEMA, max_tokens=8000, log=log)
        decisions = result["decisions"]
        decided = {d["id"] for d in decisions}
        for d in decisions:
            if d["keep"] and d["id"] in indexed:
                kept_ids.add(d["id"])
        # ids the model failed to decide default to keep (curation re-judges)
        kept_ids.update(i for i in chunk_ids if i not in decided)

    kept = [indexed[i] for i in sorted(kept_ids)]
    log("  triage: %d tier-1 pass through, %d/%d tier-2 kept"
        % (len(tier1), len(kept), len(tier2)))
    return tier1 + kept
