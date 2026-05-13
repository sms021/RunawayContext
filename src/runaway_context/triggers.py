"""Trigger-based capture heuristics (E17).

This module supplies the heuristics that an AI integration can call to decide
whether a moment in a conversation looks like "scar tissue" worth proposing as
a lesson draft. **It never writes to ``lessons_learned`` directly.** HR-3:
all candidate captures land in the ``lesson_drafts`` inbox for human review.

Public surface:

* :data:`STRONG_TRIGGERS` / :data:`WEAK_TRIGGERS` — module-level phrase lists
* :func:`detect_triggers` — scan free text and emit one record per match
* :func:`suggest_lesson_from_conversation` — propose a structured lesson draft
* :func:`auto_propose` — call the suggestion engine and insert into the inbox

Refuses:
    Any direct write to ``lessons_learned``. The :func:`auto_propose` path
    delegates exclusively to ``Client.propose_lesson_draft``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------- trigger sets

# Strong triggers — phrases that almost certainly mark scar tissue.
STRONG_TRIGGERS: Tuple[str, ...] = (
    "we got burned",
    "we just got burned",
    "this broke prod",
    "this broke production",
    "took down prod",
    "took down production",
    "the fix was non-obvious",
    "the fix was nonobvious",
    "the fix was not obvious",
    "ran for hours before",
    "ran for hours before we",
    "lesson learned",
    "never again",
    "this should never happen again",
    "we got bitten",
    "we got bit by",
    "burned us last",
    "scar tissue",
    "post-mortem",
    "post mortem",
    "root cause was",
    "data loss",
    "lost data",
    "silently failed",
    "silent failure",
    "wiped the",
)

# Weak triggers — softer signals, useful in combination.
WEAK_TRIGGERS: Tuple[str, ...] = (
    "we didn't know",
    "we did not know",
    "we didn't realize",
    "we did not realize",
    "going forward",
    "from now on",
    "next time",
    "in future",
    "in the future",
    "should have",
    "shouldn't have",
    "should not have",
    "we should",
    "we need to",
    "make sure to",
    "always remember",
    "always check",
    "do not forget",
    "don't forget",
    "gotcha",
    "watch out for",
    "be careful with",
    "tripped over",
    "tripped up",
    "caught us by surprise",
    "took a while to find",
    "spent hours",
    "spent days",
    "wasted hours",
    "wasted a day",
)

# Severity heuristics: phrases bumping the suggested blast_radius.
_HIGH_BLAST_HINTS: Tuple[str, ...] = (
    "prod",
    "production",
    "data loss",
    "wiped",
    "outage",
    "took down",
    "customer",
)
_HIGH_FREQ_HINTS: Tuple[str, ...] = (
    "every time",
    "always",
    "repeatedly",
    "keeps happening",
    "happens again",
)
_LOW_REVERSIBILITY_HINTS: Tuple[str, ...] = (
    "no rollback",
    "can't undo",
    "cannot undo",
    "irrecoverable",
    "permanent",
    "wiped",
    "data loss",
)

_IMPERATIVE_VERBS: Tuple[str, ...] = (
    "always",
    "never",
    "do not",
    "don't",
    "make sure",
    "ensure",
    "verify",
    "check",
    "require",
    "validate",
    "log",
    "use",
    "avoid",
    "prefer",
    "stop",
    "remember",
)

# Sentence splitter: deliberately simple — stdlib only (HR-1).
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


# ---------------------------------------------------------------- helpers

def _normalize(text: str) -> str:
    """Lower-case the text for case-insensitive phrase matching.

    Returns:
        Lower-cased copy of the input (or empty string when input is None).

    Refuses:
        Nothing.
    """
    if not text:
        return ""
    return text.lower()


def _suggest_severity(snippet: str) -> Dict[str, Optional[int]]:
    """Compute suggested severity axes from a snippet's vocabulary.

    Returns:
        A dict ``{blast_radius, frequency, reversibility}`` (each int or None).
        Axes are mid-range (3) when one matching hint is present, and pushed
        to 4-5 when multiple hints or particularly heavy phrases match.

    Refuses:
        Nothing.
    """
    lowered = _normalize(snippet)
    blast = None
    freq = None
    rev = None

    high_blast_hits = sum(1 for h in _HIGH_BLAST_HINTS if h in lowered)
    if high_blast_hits:
        blast = 5 if high_blast_hits >= 2 else 4

    high_freq_hits = sum(1 for h in _HIGH_FREQ_HINTS if h in lowered)
    if high_freq_hits:
        freq = 4 if high_freq_hits >= 2 else 3

    low_rev_hits = sum(1 for h in _LOW_REVERSIBILITY_HINTS if h in lowered)
    if low_rev_hits:
        rev = 5 if low_rev_hits >= 2 else 4

    return {"blast_radius": blast, "frequency": freq, "reversibility": rev}


def _find_phrase_spans(text: str, phrases: Tuple[str, ...]) -> List[Tuple[int, int, str]]:
    """Return ``(start, end, phrase)`` for every match of any phrase in text.

    Returns:
        List of triples sorted by start index.

    Refuses:
        Nothing.
    """
    lowered = _normalize(text)
    spans: List[Tuple[int, int, str]] = []
    for phrase in phrases:
        if not phrase:
            continue
        start = 0
        while True:
            idx = lowered.find(phrase, start)
            if idx < 0:
                break
            spans.append((idx, idx + len(phrase), phrase))
            start = idx + max(1, len(phrase))
    spans.sort(key=lambda s: s[0])
    return spans


def _snippet_for(text: str, start: int, end: int, window: int = 80) -> str:
    """Slice a centered snippet of ``text`` around ``[start, end]``.

    Returns:
        At most ``2 * window + (end - start)`` characters of context.

    Refuses:
        Nothing.
    """
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    snippet = text[lo:hi].strip()
    return snippet


def _split_sentences(text: str) -> List[str]:
    """Split ``text`` into rough sentences (stdlib only)."""
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _index_to_sentence(sentences: List[str], full_text: str, idx: int) -> int:
    """Return the index of the sentence in ``sentences`` that contains char ``idx``."""
    cursor = 0
    for i, s in enumerate(sentences):
        loc = full_text.find(s, cursor)
        if loc < 0:
            continue
        if loc <= idx < loc + len(s):
            return i
        cursor = loc + len(s)
    return -1


# ---------------------------------------------------------------- public API

def detect_triggers(text: str) -> List[Dict[str, Any]]:
    """Scan ``text`` for trigger phrases and return one record per match.

    Each record has::

        {
            "trigger": "<phrase that matched>",
            "snippet": "<context window>",
            "suggested_severity": {"blast_radius": int|None,
                                   "frequency": int|None,
                                   "reversibility": int|None},
            "strength": "strong" | "weak",
            "start": int,
            "end": int,
        }

    Returns:
        A list of trigger records (may be empty); strong triggers come first
        in document order, then weak triggers in document order. Duplicate
        phrase hits are preserved (callers may dedupe).

    Refuses:
        Nothing — non-string input becomes an empty list.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    results: List[Dict[str, Any]] = []
    strong_spans = _find_phrase_spans(text, STRONG_TRIGGERS)
    weak_spans = _find_phrase_spans(text, WEAK_TRIGGERS)

    for start, end, phrase in strong_spans:
        snippet = _snippet_for(text, start, end)
        results.append({
            "trigger": phrase,
            "snippet": snippet,
            "suggested_severity": _suggest_severity(snippet),
            "strength": "strong",
            "start": start,
            "end": end,
        })
    for start, end, phrase in weak_spans:
        snippet = _snippet_for(text, start, end)
        results.append({
            "trigger": phrase,
            "snippet": snippet,
            "suggested_severity": _suggest_severity(snippet),
            "strength": "weak",
            "start": start,
            "end": end,
        })
    return results


def suggest_lesson_from_conversation(
    transcript: str,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Synthesise a candidate lesson draft from a conversation transcript.

    Strategy:
      1. Run :func:`detect_triggers` over the transcript.
      2. Score: 5 points per strong hit, 1 point per weak hit.
      3. Pick the strongest hit (earliest strong if any, else earliest weak).
      4. ``title`` = first sentence after the trigger, truncated to 80 chars.
      5. ``what_happened`` = surrounding context (~400 chars) around the hit.
      6. ``prevention_rule`` = first sentence in the surrounding context that
         starts with an imperative verb (see :data:`_IMPERATIVE_VERBS`); else
         the sentence containing the trigger itself.

    Returns:
        A dict with keys ``confidence`` (float 0..1), ``strong_hits``,
        ``weak_hits``, ``best_trigger``, ``title``, ``what_happened``,
        ``prevention_rule``, ``suggested_severity``, ``source_conversation_ref``.
        ``confidence`` is 1.0 for any strong hit, 0.7 for 3+ weak hits, 0.3
        for 1-2 weak hits, 0.0 when nothing matches.

    Refuses:
        Nothing — returns a zero-confidence stub when no triggers fire.
    """
    triggers = detect_triggers(transcript or "")
    strong = [t for t in triggers if t["strength"] == "strong"]
    weak = [t for t in triggers if t["strength"] == "weak"]

    if not strong and not weak:
        return {
            "confidence": 0.0,
            "strong_hits": 0,
            "weak_hits": 0,
            "best_trigger": None,
            "title": None,
            "what_happened": None,
            "prevention_rule": None,
            "suggested_severity": {"blast_radius": None,
                                   "frequency": None,
                                   "reversibility": None},
            "source_conversation_ref": conversation_id,
        }

    best = strong[0] if strong else weak[0]
    if strong:
        confidence = 1.0
    elif len(weak) >= 3:
        confidence = 0.7
    else:
        confidence = 0.3

    sentences = _split_sentences(transcript)
    trigger_idx = _index_to_sentence(sentences, transcript, best["start"])

    # title: the sentence containing the trigger (or the next one if very short)
    title: Optional[str] = None
    if 0 <= trigger_idx < len(sentences):
        anchor = sentences[trigger_idx]
        if len(anchor) < 20 and trigger_idx + 1 < len(sentences):
            anchor = sentences[trigger_idx + 1]
        title = anchor[:80].rstrip(" .!?") or None

    # what_happened: 400-char window centered on the trigger
    lo = max(0, best["start"] - 200)
    hi = min(len(transcript), best["end"] + 200)
    what_happened = transcript[lo:hi].strip() or None

    # prevention_rule: first sentence in window starting with an imperative
    prevention_rule: Optional[str] = None
    window_sentences = _split_sentences(what_happened or "")
    for s in window_sentences:
        lowered = s.lower().lstrip("- *>")
        for verb in _IMPERATIVE_VERBS:
            if lowered.startswith(verb + " ") or lowered.startswith(verb + ","):
                prevention_rule = s.strip()
                break
        if prevention_rule:
            break
    if prevention_rule is None and 0 <= trigger_idx < len(sentences):
        prevention_rule = sentences[trigger_idx].strip()

    return {
        "confidence": confidence,
        "strong_hits": len(strong),
        "weak_hits": len(weak),
        "best_trigger": best["trigger"],
        "title": title,
        "what_happened": what_happened,
        "prevention_rule": prevention_rule,
        "suggested_severity": best["suggested_severity"],
        "source_conversation_ref": conversation_id,
    }


def auto_propose(
    client: Any,
    transcript: str,
    project_tags: List[str],
    conversation_id: Optional[str] = None,
    proposed_by: Optional[str] = None,
) -> Optional[int]:
    """Auto-propose a lesson draft if the transcript clears the confidence bar.

    The bar:
      * at least one **strong** trigger phrase, OR
      * at least three **weak** trigger phrases.

    On a match this function calls ``client.propose_lesson_draft(...)`` and
    returns the new draft id. The draft lands in the ``lesson_drafts`` inbox
    in status ``pending``. **No row is ever inserted into ``lessons_learned``
    by this function — HR-3.**

    Returns:
        The new draft id (int) on success, or ``None`` when the transcript
        does not pass the confidence bar (or when ``project_tags`` is empty).

    Refuses:
        Empty ``project_tags`` (returns None — caller must tag at the write,
        HR-2).
        Empty / whitespace-only transcript (returns None).
        A ``client`` that lacks ``propose_lesson_draft`` (raises
        :class:`AttributeError`).
    """
    if client is None:
        raise AttributeError("auto_propose requires a Client (got None)")
    if not hasattr(client, "propose_lesson_draft"):
        raise AttributeError(
            "client lacks propose_lesson_draft — wrong type or wrong version"
        )
    if not isinstance(transcript, str) or not transcript.strip():
        return None
    if not project_tags:
        return None

    suggestion = suggest_lesson_from_conversation(transcript, conversation_id)
    if suggestion["confidence"] < 0.7:
        return None
    if not suggestion["title"]:
        return None

    draft_id = client.propose_lesson_draft(
        title=suggestion["title"],
        project_tags=list(project_tags),
        what_happened=suggestion["what_happened"],
        prevention_rule=suggestion["prevention_rule"],
        source_conversation_ref=conversation_id,
        proposed_by=proposed_by,
    )
    return int(draft_id) if draft_id is not None else None
