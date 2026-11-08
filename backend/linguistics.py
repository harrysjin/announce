"""Morphological discovery and evidence alignment, separate from meaning choice.

Kiwi proposes a lemma and grammatical boundaries in the actual line context.
Those proposals never establish vowel length. Direct dictionary evidence wins;
the restricted rule evaluator below records its scope and keeps unsafe forms
unresolved. Offsets always refer to syllables of the original PDF word.
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


NOMINAL = {"NNG", "NNP", "NNB", "NP", "NR"}


def nominal_components(surface: str, tokens: list[dict]) -> list[dict]:
    """Use complete, literal Kiwi morphemes, never arbitrary text prefixes.

    Overlapping/contracted tokens cannot define literal component boundaries.
    NNP is retained as a hypothesis; it is not a pronunciation or sense choice.
    """
    components = []
    end = 0
    for token in tokens:
        if token["tag"].startswith("J"):
            break
        if token["tag"] not in NOMINAL | {"XSN"}:
            return []
        start, stop = token["start"], token["end"]
        if start != end or stop <= start or surface[start:stop] != token["form"]:
            return []
        components.append({"lemma": token["form"], "pos": token["tag"],
                           "start": len(re.findall(r"[가-힣]", surface[:start])),
                           "end": len(re.findall(r"[가-힣]", surface[:stop])),
                           "kind": "suffix" if token["tag"] == "XSN" else "nominal"})
        end = stop
    return components if len(components) > 1 else []


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
                    "analyzer": "Kiwi 0.24.1", "rule_inference_enabled": True,
                    "components": nominal_components(surface, tokens)}
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
            elif (following and following[0]["form"] in ("씨", "님", "군", "양") and following[0]["start"] - end <= 1):
                plan.update(kind="proper", lemma=first["form"] if surface.startswith(first["form"]) else surface)
                plan["components"] = []
                plan["proper_context"] = True
            elif any(t["tag"] in ("XSV", "XSA") for t in tokens):
                derivative = next(i for i, t in enumerate(tokens) if t["tag"] in ("XSV", "XSA"))
                roots, suffix = tokens[:derivative], tokens[derivative]
                if (roots and all(t["tag"] in NOMINAL for t in roots)
                        and roots[0]["start"] == 0
                        and all(t["tag"].startswith("E") for t in tokens[derivative + 1:])
                        and "".join(t["form"] for t in roots) == surface[:suffix["start"]]):
                    plan.update(kind="derived", lemma="".join(t["form"] for t in roots) + suffix["form"] + "다",
                                pos="VV" if suffix["tag"] == "XSV" else "VA",
                                original_pos=first["tag"], base_end=suffix["end"])
                else:
                    plan["kind"] = "complex"
            elif first["tag"] in ("VV", "VA", "VX"):
                plan.update(kind="verb", lemma=first["form"] + "다", base_end=first["end"])
            elif plan["components"]:
                plan.update(kind="compound", pos="NNG")
            elif first["tag"] == "NNP":
                plan.update(kind="proper", lemma=first["form"] if surface.startswith(first["form"]) else surface)
            elif first["tag"] in ("NNG", "NNB", "NP", "NR", "MAG", "MAJ", "MM"):
                if first["start"] == 0 and surface.startswith(first["form"]) and all(t["tag"].startswith("J") for t in tokens[1:]):
                    plan.update(kind="nominal_particle" if len(tokens) > 1 else "exact",
                                lemma=first["form"], base_end=len(first["form"]),
                                particle=surface[len(first["form"]):])
                elif any(t["tag"] in ("VCP", "VCN") for t in tokens[1:]):
                    plan["kind"] = "complex"
            output[occurrence["id"]] = plan
    return output


POS = {
    "NNG": {"명사", "의존 명사"}, "NNB": {"의존 명사", "명사"},
    "NNP": {"명사", "의존 명사"}, "XSN": {"접사"},
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


RULE_URL = "https://kornorms.korean.go.kr/regltn/regltnView.do?regltn_code=0002"
# An explicit applicability policy, not a table of answers for example words.
# §6 contraction and noninitial compound length, and §7 shortening/lexical
# exceptions require surface-form dictionary evidence; this evaluator does not
# generate them from the lemma. In particular vowel-initial endings and further
# predicate-forming suffixes are outside the invariant-stem rule.
RULE_POLICY = {
    "6": {"scope": "단어 첫음절의 장단; 합성어 비첫음절 장음·모음 축약은 별도 근거 필요",
          "exceptions": "합성어의 비첫음절 장음은 일괄 단음화하지 않고 보류"},
    "7": {"scope": "단음화 및 예외를 검사; 원형과 같은 어간 + 자음 시작 어미에만 유지 추론",
          "exceptions": "단음화의 어휘별 예외는 사전의 실제 활용 발음으로 처리",
          "excluded": ["한 음절 어간 + 모음 시작 어미", "피동·사동 등 추가 파생 접미사", "축약·어간 교체"]},
}


def rule_record(section: str, explanation: str) -> dict:
    return {"id": f"standard-pronunciation-{section}", "label": f"표준 발음법 제{section}항 적용 범위",
            "url": RULE_URL, "explanation": explanation, "policy": RULE_POLICY[section]}


def form_evidence(candidate: dict, surface: str, plan: dict) -> dict | None:
    """Direct actual-form pronunciation first, then one narrow inflection rule.

    A matching lemma is not sufficient: original stem syllables must be literal
    and unchanged, and every remaining morpheme must be an ending beginning in
    a consonant. No fabricated surface pronunciation is returned.
    """
    direct = candidate_evidence(candidate, surface)
    if direct is not None:
        return direct
    if (plan.get("kind") not in ("verb", "derived") or candidate["word"] != plan.get("lemma")
            or not candidate["word"].endswith("다")):
        return None
    stem = candidate["word"][:-1]
    if not stem or not surface.startswith(stem) or surface == stem:
        return None
    ending = surface[len(stem):]
    if not re.fullmatch(r"[가-힣]+", ending) or (ord(ending[0]) - 0xAC00) // 588 == 11:
        return None  # vowel-initial endings: shortening/exceptions/contraction
    tokens = plan.get("tokens", [])
    endings = [t for t in tokens if t["end"] > len(stem)]
    if not endings or any(not t["tag"].startswith("E") or t["start"] < len(stem) for t in endings):
        return None  # includes overlaps, stem changes, or another derivation
    base = candidate_evidence(candidate, candidate["word"])
    if base is None or any(p >= len(stem) for p in base["positions"]):
        return None
    return {**base, "form": candidate["word"], "surface": surface,
            "evidence_type": "rule_inference", "pronunciation_scope": candidate["word"],
            "explanation": "기본형의 원문 어간이 그대로이고 자음 시작 어미만 결합하여 장단을 유지하는 규칙 판정",
            "applied_rules": [rule_record("6", "첫음절을 포함한 기본형의 확인된 장음 위치를 원문 어간에 연결합니다."),
                              rule_record("7", "모음 시작 어미·추가 파생 접미사·축약·어간 교체에는 이 유지 규칙을 적용하지 않습니다.")]}


def form_consensus(candidates: list[dict], surface: str, plan: dict) -> dict | None:
    if not candidates:
        return None
    evidence = [form_evidence(c, surface, plan) for c in candidates]
    if any(e is None for e in evidence) or any(e["positions"] != evidence[0]["positions"] for e in evidence[1:]):
        return None
    inferred = any(e["evidence_type"] == "rule_inference" for e in evidence)
    rules = {r["id"]: r for e in evidence for r in e.get("applied_rules", [])}
    return {"positions": evidence[0]["positions"], "evidence": evidence,
            "evidence_candidates": [c["id"] for c in candidates],
            "pronunciations": list(dict.fromkeys(p for e in evidence for p in e["pronunciations"])),
            "evidence_type": "rule_consensus" if inferred else "candidate_consensus",
            "pronunciation_scope": plan.get("lemma", surface) if inferred else surface,
            "applied_rules": list(rules.values())}


def compound_evidence(components: list[dict], surface: str) -> dict:
    """Combine length evidence without inventing a whole-word pronunciation.

    Later members with lexical length stay unresolved: §6 has compound
    exceptions, so their length cannot be discarded or copied indiscriminately.
    Missing/ambiguous components never establish an all-short whole word.
    """
    positions, checked, unresolved, evidence, ids = [], [], [], [], []
    for component in components:
        start, end = component["start"], component["end"]
        candidates = component["candidates"]
        agreed = consensus_evidence(candidates, component["lemma"])
        if agreed is None or (start > 0 and agreed["positions"]):
            component.update(status="unresolved", long_positions=[], evidence_candidates=[],
                             reason="후보의 장단이 상충하거나 근거가 부족합니다. 비첫음절 장음은 합성어 전체 근거가 필요합니다.")
            unresolved.append([start, end])
            continue
        mapped = [start + i for i in agreed["positions"]]
        component.update(status="long" if mapped else "none", long_positions=mapped,
                         evidence_candidates=agreed["evidence_candidates"], reason="구성 요소의 모든 허용 후보가 장단에 동의합니다.")
        checked.append([start, end])
        positions.extend(mapped)
        evidence.extend({**e, "start": start, "end": end, "scope": component["lemma"]} for e in agreed["evidence"])
        ids.extend(agreed["evidence_candidates"])
    syllables = len(re.findall(r"[가-힣]", surface))
    covered = {i for start, end in checked + unresolved for i in range(start, end)}
    for i in range(syllables):
        if i not in covered:
            unresolved.append([i, i + 1])
    return {"positions": sorted(set(positions)), "evidence": evidence,
            "evidence_candidates": list(dict.fromkeys(ids)), "pronunciations": [],
            "pronunciation_scope": "구성 요소별 발음 (전체 단어 발음 아님)",
            "evidence_type": "rule_inference", "explanation": "원문 형태소 경계와 구성 요소의 사전 발음을 연결한 규칙 판정",
            "applied_rules": [rule_record("6", "첫 구성 요소의 장단을 원문 범위에 연결합니다. 뒤 구성 요소의 장음은 합성어의 예외 가능성이 있어 전체 근거 없이 옮기거나 단음화하지 않습니다.")],
            "coverage": {"complete": not unresolved and bool(checked), "checked_ranges": checked, "unresolved_ranges": unresolved}}
