"""Morphological discovery and evidence alignment, separate from meaning choice.

Kiwi proposes a lemma and grammatical boundaries in the actual line context.
Those proposals never establish vowel length: dictionary pronunciations of the
exact surface form must still agree. No general noun/verb length rules are
enabled here without an inspected authoritative rule source.
"""
from __future__ import annotations

import re
import threading

from .dictionary import long_positions, pronunciation_for


_ANALYZER = None
_MODEL_LOCK = threading.Lock()
_TOKEN_LOCK = threading.Lock()


def analyzer():
    global _ANALYZER
    # Concurrent queued jobs must not each load an 88MB model on first use.
    with _MODEL_LOCK:
        if _ANALYZER is None:
            from kiwipiepy import Kiwi
            # Retain the standard lexicon/model without optional typo or
            # WikiData phrase expansion: PDF transcription stays literal and
            # low-memory deployment does not load these auxiliary dictionaries.
            _ANALYZER = Kiwi(num_workers=1, load_typo_dict=False, load_multi_dict=False)
        return _ANALYZER


def base_tag(tag: str) -> str:
    return tag.split("-", 1)[0]


def line_analysis(line: str) -> list[dict]:
    with _TOKEN_LOCK:
        return [{"form": token.form, "tag": base_tag(token.tag), "start": token.start,
                 "end": token.end, "surface": line[token.start:token.end]}
                for token in analyzer().tokenize(line)]


def occurrence_plans(job: dict) -> dict[str, dict]:
    output = {}
    cache = {}
    for page in job["pages"]:
        lines = {}
        glyph_offsets = {}
        for glyph in page["glyphs"]:
            line = glyph.get("line", 0)
            start = len(lines.get(line, ""))
            lines[line] = lines.get(line, "") + glyph["text"]
            glyph_offsets[glyph["id"]] = (line, start, start + len(glyph["text"]))
        for occurrence in (o for o in job["occurrences"] if o["page"] == page["number"]):
            spans = [glyph_offsets[g] for g in occurrence["glyph_ids"] if g in glyph_offsets]
            surface = occurrence["text"]
            if not spans or len({s[0] for s in spans}) != 1:
                output[occurrence["id"]] = {"kind": "complex", "lemma": surface, "pos": "", "reason": "원문 범위가 여러 줄에 걸칩니다."}
                continue
            line_id = spans[0][0]
            line = lines[line_id]
            start, end = min(s[1] for s in spans), max(s[2] for s in spans)
            if line not in cache:
                cache[line] = line_analysis(line)
            tokens = [t for t in cache[line] if t["start"] < end and t["end"] > start]
            tokens = [{**t, "start": t["start"] - start, "end": t["end"] - start} for t in tokens]
            plan = {"kind": "exact", "lemma": surface, "pos": "", "base_start": 0,
                    "base_end": len(surface), "particle": "", "tokens": tokens,
                    "analyzer": "Kiwi 0.24.1", "rule_inference_enabled": False}
            if not tokens:
                output[occurrence["id"]] = plan
                continue
            first = tokens[0]
            plan["pos"] = first["tag"]
            following = [t for t in cache[line] if t["start"] >= end]
            if any(c.isdigit() for c in surface):
                plan["kind"] = "number"
            elif re.search(r"[A-Za-z]", surface):
                plan["kind"] = "foreign"
            elif (first["tag"] == "NNP" or
                  (following and following[0]["form"] in ("씨", "님", "군", "양") and following[0]["start"] - end <= 1)):
                plan.update(kind="proper", lemma=first["form"] if surface.startswith(first["form"]) else surface)
            elif first["tag"] in ("VV", "VA", "VX"):
                plan.update(kind="verb", lemma=first["form"] + "다", base_end=first["end"])
            elif first["tag"] in ("NNG", "NNB", "NP", "NR", "MAG", "MAJ", "MM"):
                if first["start"] == 0 and surface.startswith(first["form"]) and all(t["tag"].startswith("J") for t in tokens[1:]):
                    plan.update(kind="nominal_particle" if len(tokens) > 1 else "exact",
                                lemma=first["form"], base_end=len(first["form"]),
                                particle=surface[len(first["form"]):])
                elif any(t["tag"] in ("XSV", "XSA", "VCP", "VCN") for t in tokens[1:]):
                    # Compound derivations/copulas are not a suffix-removal rule.
                    # They remain whole expressions unless directly attested.
                    plan["kind"] = "complex"
            output[occurrence["id"]] = plan
    return output


POS = {
    "NNG": {"명사", "의존 명사"}, "NNB": {"의존 명사", "명사"},
    "NP": {"대명사"}, "NR": {"수사"}, "MAG": {"부사"}, "MAJ": {"부사"}, "MM": {"관형사"},
    "VV": {"동사"}, "VA": {"형용사"}, "VX": {"보조 동사", "보조 형용사", "동사", "형용사"},
}


def candidates_for_plan(candidates: list[dict], plan: dict) -> list[dict]:
    allowed = POS.get(plan.get("pos"))
    if not allowed:
        return candidates
    # Missing POS must remain admissible, rather than being silently excluded.
    return [c for c in candidates if not c.get("pos") or c["pos"] in allowed]


def candidate_evidence(candidate: dict, surface: str) -> dict | None:
    reading = pronunciation_for(candidate, surface)
    if reading is None:
        return None
    pronunciations, explanation = reading
    positions = long_positions(pronunciations)
    if positions is None:
        return None
    syllable_count = len(re.findall(r"[가-힣]", surface))
    if not syllable_count or any(len(re.findall(r"[가-힣]", p)) != syllable_count for p in pronunciations):
        return None
    return {"candidate_id": candidate["id"], "positions": positions,
            "pronunciations": pronunciations, "form": surface, "explanation": explanation,
            "evidence_type": "dictionary_direct" if surface == candidate["word"] else "dictionary_inflection"}


def consensus_evidence(candidates: list[dict], surface: str) -> dict | None:
    """All admissible meanings must have direct evidence of this exact form.

    An omitted candidate or missing reading is an abstention, not permission to
    use the candidates that happen to have pronunciations. No meaning is chosen.
    """
    if not candidates:
        return None
    evidence = [candidate_evidence(c, surface) for c in candidates]
    if any(e is None for e in evidence):
        return None
    first = evidence[0]
    if any(e["positions"] != first["positions"] for e in evidence[1:]):
        return None
    return {"positions": first["positions"], "evidence": evidence,
            "evidence_candidates": [c["id"] for c in candidates],
            "pronunciations": list(dict.fromkeys(p for e in evidence for p in e["pronunciations"]))}
