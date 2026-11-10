"""Conservative adapter for the National Institute of Korean Language Open API.

No dictionary entries are bundled or fabricated. Search finds candidates; detail
responses, not the search summary, are the source of pronunciation evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import os
import re
import xml.etree.ElementTree as ET

import httpx

BASE = "https://stdict.korean.go.kr/api"


class DictionaryError(Exception):
    def __init__(self, message: str, *, failure_kind: str = "lookup_failed"):
        super().__init__(message)
        self.failure_kind = failure_kind


def value(node: ET.Element, path: str) -> str:
    found = node.find(path)
    return "" if found is None else "".join(found.itertext()).strip()


def xml_root(raw: bytes) -> ET.Element:
    if len(raw) > 8_000_000:
        raise DictionaryError("사전 응답 크기를 초과했습니다.")
    # ElementTree does not fetch external entities. Explicitly reject DTDs too.
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise DictionaryError("안전하지 않은 사전 응답입니다.")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise DictionaryError("사전 응답 형식을 확인할 수 없습니다.") from None
    if root.find(".//error") is not None or root.tag == "error":
        raise DictionaryError("사전 API가 요청을 거절했습니다. 인증키와 이용 한도를 확인해 주세요.")
    return root


def normalize_word(word: str) -> str:
    return re.sub(r"[\-^\d]", "", word).strip()


def parse_search(raw: bytes) -> list[str]:
    root = xml_root(raw)
    if root.find("total") is None and root.find(".//total") is None:
        raise DictionaryError("사전 검색 응답에 결과 수가 없습니다.")
    codes = []
    for item in root.findall(".//item"):
        code = value(item, "target_code")
        if code.isdigit() and code not in codes:
            codes.append(code)
    return codes


def parse_detail(raw: bytes, target: str) -> list[dict]:
    root = xml_root(raw)
    items = root.findall(".//item")
    if root.tag == "item":
        items = [root]
    if not items:
        raise DictionaryError("사전 상세 응답에 항목이 없습니다.")
    output = []
    for item in items:
        info = item.find("word_info")
        if info is None:
            info = item
        word = value(info, "word")
        if not word:
            raise DictionaryError("사전 상세 응답의 표제어가 없습니다.")
        actual_code = value(item, "target_code") or target
        pronunciations = [value(p, "pronunciation") for p in info.findall("pronunciation_info")]
        if not pronunciations:
            pronunciations = [p.text.strip() for p in info.findall("pronunciation") if p.text and p.text.strip()]
        pronunciations = list(dict.fromkeys(p for p in pronunciations if p))
        originals = info.findall("original_language_info")
        origin = " · ".join(filter(None, (value(p, "original_language") for p in originals)))
        hanja = " · ".join(filter(None, (value(p, "original_language") for p in originals if value(p, "language_type") == "한자")))
        inflection_map: dict[tuple[str, str], list[str]] = {}
        for inf in info.findall("conju_info"):
            # conju_info can contain both a full form and a contraction, e.g.
            # 하여[하여] and 해[해ː]. Their pronunciations must never be pooled.
            blocks = [(node, "conjugation") for node in inf.findall("conjugation_info")]
            blocks += [(node, "abbreviation") for node in inf.findall("abbreviation_info")]
            if not blocks:
                blocks = [(inf, "conjugation")]
            for block, form_type in blocks:
                form = value(block, "conjugation" if form_type == "conjugation" else "abbreviation")
                readings = [p.text.strip() for p in block.findall(".//pronunciation") if p.text and p.text.strip()]
                if form:
                    inflection_map.setdefault((form, form_type), []).extend(readings)
        inflections = [{"form": form, "type": kind, "pronunciations": list(dict.fromkeys(readings))}
                       for (form, kind), readings in inflection_map.items()]
        # A dictionary lexeme may contain several parts of speech. Keep each
        # meaning attached to its own pos_info instead of copying the first POS.
        pos_blocks = info.findall("pos_info") or [info]
        sense_records = [(sense, value(pos, "pos") or value(info, "pos_info/pos"))
                         for pos in pos_blocks for sense in (pos.findall(".//sense_info") or [pos])]
        for i, (sense, pos) in enumerate(sense_records):
            sense_code = value(sense, "sense_code") or value(sense, "sense_no") or str(i + 1)
            definition = value(sense, "definition")
            output.append({
                "id": f"{actual_code}:{sense_code}", "target_code": actual_code,
                "sense_code": sense_code, "word": normalize_word(word),
                "display_word": word, "origin": origin, "hanja": hanja,
                "pronunciations": pronunciations, "pronunciation": " / ".join(pronunciations),
                "definition": definition, "pos": pos,
                "inflections": inflections,
                "url": f"https://stdict.korean.go.kr/search/searchView.do?word_no={actual_code}&searchKeywordTo=3",
                "queried_at": datetime.now(timezone.utc).isoformat(),
            })
    return output


def long_positions(pronunciations: list[str]) -> list[int] | None:
    """Return syllable offsets only if every supplied reading agrees.

    Colon is accepted only inside the API pronunciation field, never a definition.
    Multiple variants disagreeing in length remain unresolved.
    """
    if not pronunciations:
        return None
    options = []
    for pronunciation in pronunciations:
        offsets = []
        syllable = -1
        for ch in pronunciation:
            if "가" <= ch <= "힣":
                syllable += 1
            elif ch in ("ː", ":"):
                if syllable < 0:
                    return None
                offsets.append(syllable)
        if syllable < 0:
            return None
        options.append(offsets)
    return options[0] if all(p == options[0] for p in options) else None


def pronunciation_for(candidate: dict, surface: str) -> tuple[list[str], str] | None:
    if surface == candidate["word"]:
        return candidate["pronunciations"], "사전 기본형 발음"
    for inf in candidate.get("inflections", []):
        if inf["form"] == surface and inf["pronunciations"]:
            return inf["pronunciations"], "사전에 명시된 활용 발음"
    return None


class DictionaryClient:
    def __init__(self):
        self.cache: dict[str, list[dict]] = {}
        # A headword miss must not hide a later auxiliary search for the same
        # surface, nor may auxiliary hits replace exact headword evidence.
        self.inflection_cache: dict[str, list[dict]] = {}

    async def _get(self, client, url: str, params: dict):
        # Retry only temporary failures of idempotent reads, once. Never retry
        # authentication/policy denial or malformed dictionary evidence.
        for attempt in range(2):
            try:
                response = await client.get(url, params=params)
                if response.status_code in (502, 503, 504) and attempt == 0:
                    await asyncio.sleep(0.4)
                    continue
                response.raise_for_status()
                return response
            except httpx.TransportError:
                if attempt:
                    raise
                await asyncio.sleep(0.4)

    async def lookup(self, word: str) -> list[dict]:
        """Keep the original exact headword search as the primary lookup."""
        return await self._lookup(word, inflections=False)

    async def lookup_inflections(self, word: str) -> list[dict]:
        """Search the documented target=5 index for auxiliary lemma candidates.

        The search does not generate arbitrary inflections. A hit establishes
        candidate discovery only: pronunciation_for must still find the exact
        requested form in that entry's detail response before calling it direct
        dictionary pronunciation evidence.
        """
        return await self._lookup(word, inflections=True)

    async def _lookup(self, word: str, *, inflections: bool) -> list[dict]:
        cache = self.inflection_cache if inflections else self.cache
        if word in cache:
            return cache[word]
        key = os.environ.get("STDICT_API_KEY")
        if not key:
            raise DictionaryError("표준국어대사전 인증키가 연결되지 않았습니다.")
        try:
            async with httpx.AsyncClient(timeout=20, trust_env=True, follow_redirects=False) as client:
                search_params = {
                    "key": key, "q": word, "req_type": "xml", "num": 100,
                    "advanced": "y", "method": "exact", "type1": "word",
                }
                if inflections:
                    search_params["target"] = 5
                response = await self._get(client, f"{BASE}/search.do", params=search_params)
                response.raise_for_status()
                codes = parse_search(response.content)
                # A truncated candidate set must never be treated as unambiguous.
                root = xml_root(response.content)
                total = int(value(root, "total") or "0")
                if total > 100:
                    raise DictionaryError("사전 후보가 너무 많아 문맥 검토가 필요합니다.")
                candidates = []
                for code in codes:
                    detail = await self._get(client, f"{BASE}/view.do", params={
                        "key": key, "q": code, "req_type": "xml", "method": "target_code",
                    })
                    detail.raise_for_status()
                    for candidate in parse_detail(detail.content, code):
                        candidate["lookup_provenance"] = {
                            "type": "inflection_search" if inflections else "exact",
                            "query": word,
                            "target": 5 if inflections else 1,
                        }
                        candidates.append(candidate)
        except httpx.TimeoutException:
            raise DictionaryError("사전 연결에 실패했습니다. 네트워크·인증키·응답 형식을 확인해 주세요.",
                                  failure_kind="lookup_timeout") from None
        except (httpx.HTTPError, ValueError):
            # Never propagate exception URLs: they can contain the API key.
            raise DictionaryError("사전 연결에 실패했습니다. 네트워크·인증키·응답 형식을 확인해 주세요.") from None
        cache[word] = candidates
        return candidates
