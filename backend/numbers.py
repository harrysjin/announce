"""Conservative Arabic numeral reading, dictionary evidence and glyph alignment.

The lexical tables below spell numerals, not their vowel lengths. Length comes
only from the official dictionary. A possible reading is distinct from a chosen
reading; generated pronunciation and uncertain digit/syllable alignment never
create a circle. All source offsets are original Python character offsets.
"""
from __future__ import annotations

from copy import deepcopy
import re

from .linguistics import consensus_evidence, line_analysis, rule_record

SINO = ("영", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구")
HANJA = ("零", "一", "二", "三", "四", "五", "六", "七", "八", "九")
SMALL = (("", ""), ("십", "十"), ("백", "百"), ("천", "千"))
LARGE = (("", ""), ("만", "萬"), ("억", "億"), ("조", "兆"))
NATIVE = ("", "하나", "둘", "셋", "넷", "다섯", "여섯", "일곱", "여덟", "아홉")
NATIVE_MODIFIER = {1: "한", 2: "두", 3: "세", 4: "네"}
NATIVE_TENS = ("", "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔")
# Semantic unit classes are reading-discovery rules, not a pronunciation list.
NATIVE_COUNTERS = {"명": "名", "개": "個", "마리": "", "권": "卷", "장": "張", "대": "臺", "살": ""}
SINO_UNITS = {"년": "年", "월": "月", "일": "日", "시": "時", "분": "分", "초": "秒", "원": "", "번": "番", "등": "等", "퍼센트": "percent"}
NUMERAL_LITERAL = r"(?:[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+)"


def _segment(text, start, end, *, role="numeral", hanja="", pos="수사", group=0, exact=None):
    if exact is None:
        exact = end - start == len(text) == 1
    return {"text": text, "query": text, "source_start": start, "source_end": end,
            "role": role, "hanja": hanja, "pos": pos, "word_group": group, "exact": exact}


def _sino_integer(digits: str, start: int = 0, group: int = 0) -> list[dict] | None:
    if not digits.isascii() or not digits.isdigit() or len(digits) > 16:
        return None
    if len(digits) > 1 and digits[0] == "0":
        return None  # a code/identifier is not an integer with disposable zeros
    if int(digits) == 0:
        return [_segment("영", start, start + len(digits), hanja="零", pos="명사", group=group)]
    parts = []
    # Four-place groups preserve the source digit identity even when 일 is
    # omitted before 십/백/천 or place-value units have no one-digit reading.
    for offset, digit in enumerate(digits):
        value = int(digit)
        power = len(digits) - offset - 1
        local, large = power % 4, power // 4
        if value:
            if not (value == 1 and local):
                parts.append(_segment(SINO[value], start + offset, start + offset + 1,
                                      hanja=HANJA[value], group=group, exact=True))
            if local:
                text, hanja = SMALL[local]
                parts.append(_segment(text, start + offset, start + offset + local + 1,
                                      role="place_value", hanja=hanja, group=group, exact=False))
        if local == 0 and large:
            group_start = max(0, offset - 3)
            block = digits[group_start:offset + 1]
            if int(block):
                text, hanja = LARGE[large]
                # Korean omits 일 for exactly 1만, unlike 1억 and 1조.
                if large == 1 and block == "1" and parts and parts[-1]["text"] == "일":
                    parts.pop()
                parts.append(_segment(text, start + group_start, start + offset + 1,
                                      role="place_value", hanja=hanja, group=group, exact=False))
    return parts


def _native_integer(digits: str, start=0, group=0, modifier=False) -> list[dict] | None:
    if not digits.isascii() or not digits.isdigit() or len(digits) > 2 or (len(digits) > 1 and digits[0] == "0"):
        return None
    number = int(digits)
    if not 1 <= number <= 99:
        return None
    tens, ones = divmod(number, 10)
    parts = []
    if tens:
        word = "스무" if number == 20 and modifier else NATIVE_TENS[tens]
        # The native lexical ten is not a spelling of a particular one of the
        # two source digits. A long 쉰/열 cannot be placed arbitrarily on 5/1.
        pos = "관형사" if modifier and not ones else "수사"
        parts.append(_segment(word, start, start + len(digits), role="native", pos=pos, group=group, exact=False))
    if ones:
        word = NATIVE_MODIFIER.get(ones, NATIVE[ones]) if modifier else NATIVE[ones]
        pos = "관형사" if modifier else "수사"
        index = start + len(digits) - 1
        parts.append(_segment(word, index, index + 1, role="native", pos=pos, group=group,
                              exact=len(word) == 1))
    return parts


def _sino_literal(literal, start=0, group=0, *, padded=False):
    """Read a validated literal while retaining commas and padding offsets."""
    digit_offsets = [i for i, char in enumerate(literal) if char in "0123456789"]
    digits = "".join(literal[i] for i in digit_offsets)
    if padded and len(digits) > 1 and digits.startswith("0"):
        significant = digits.lstrip("0")
        if not significant:
            return [_segment("영", start, start + len(literal), hanja="零",
                             pos="명사", group=group, exact=False)]
        digit_offsets = digit_offsets[len(digits) - len(significant):]
        digits = significant
    parts = _sino_integer(digits, group=group)
    if parts is not None:
        for part in parts:
            first, end = part["source_start"], part["source_end"]
            part.update(source_start=start + digit_offsets[first],
                        source_end=start + digit_offsets[end - 1] + 1)
    return parts


def _unit(text, start, group=1, *, meaning="", inserted=False):
    part = _segment(text, start, start + (0 if inserted else len(text)), role="unit",
                    hanja=NATIVE_COUNTERS.get(text, SINO_UNITS.get(text, "")),
                    pos="의존 명사", group=group, exact=len(text) == 1 and not inserted)
    # The modern Korean 원 entry has no Hanja origin, unlike historical 圓/元.
    # 분 can also be a native honorific person counter, not the minute 分.
    if text == "원" or (text == "분" and meaning == "people"):
        part.update(hanja="", origin_policy="empty")
    if text == "분":
        part["meaning"] = meaning or "time"
    return part


def _option(identifier, system, label, segments, *, review_reason=""):
    segments = deepcopy(segments)
    syllable = 0
    alignment = []
    words = []
    previous = None
    for segment in segments:
        count = len(re.findall(r"[가-힣]", segment["text"]))
        segment.update(reading_start=syllable, reading_end=syllable + count)
        alignment.append({key: segment[key] for key in ("reading_start", "reading_end", "source_start", "source_end", "exact")})
        alignment[-1].update({key: segment[key] for key in ("source_location", "context_start", "context_end", "context_text", "reason")
                              if key in segment})
        if previous is not None and previous != segment["word_group"]:
            words.append(" ")
        words.append(segment["text"])
        previous = segment["word_group"]
        syllable += count
    return {"id": identifier, "system": system, "label": label, "reading": "".join(words),
            "segments": segments, "alignment": alignment, "review_reason": review_reason}


def _digit_option(digits: str, start=0, *, zero="공", identifier="digits", label="숫자 한 자리씩 읽기", review_reason="전화번호·식별자의 낭독 경계와 장음 실현을 확인해 주세요."):
    segments = []
    for index, char in enumerate(digits):
        if char.isascii() and char.isdigit():
            text = zero if char == "0" else SINO[int(char)]
            segments.append(_segment(text, start + index, start + index + 1,
                                     hanja="空" if char == "0" and zero == "공" else HANJA[int(char)],
                                     pos="명사" if char == "0" else "수사", exact=True))
    return _option(identifier, "digits", label, segments, review_reason=review_reason)


def _unit_particle_boundary(surface: str, morphology: dict | None) -> dict | None:
    """Use literal Kiwi boundaries, never a unit-looking string prefix.

    Only a known nominal unit followed exclusively by uncontracted particles
    is accepted. Copulas, endings, lexical suffixes, overlaps and gaps remain
    outside this rule. Supplied line-context tokens are reused unchanged.
    """
    if not re.search(r"[가-힣]$", surface) or not re.match(r"[0-9]", surface):
        return None
    tokens = morphology.get("tokens", []) if morphology is not None else line_analysis(surface)
    if not tokens:
        return None
    end = 0
    for token in tokens:
        start, stop = token["start"], token["end"]
        if start != end or stop <= start or surface[start:stop] != token["form"]:
            return None
        end = stop
    if end != len(surface):
        return None
    index = len(tokens)
    while index and tokens[index - 1]["tag"].startswith("J"):
        index -= 1
    if not 0 < index < len(tokens):
        return None
    unit = tokens[index - 1]
    if (unit["tag"] not in ("NNB", "NNG")
            or unit["form"] not in NATIVE_COUNTERS.keys() | SINO_UNITS.keys()
            or not re.fullmatch(NUMERAL_LITERAL, surface[:unit["start"]])):
        return None
    start = tokens[index]["start"]
    return {"text": surface[start:], "start": start, "end": len(surface),
            "tokens": deepcopy(tokens[index:]),
            "unit": {"lemma": unit["form"], "pos": unit["tag"],
                     "start": unit["start"], "end": unit["end"]},
            "analyzer": "Kiwi 0.24.1"}


def _following_unit_boundary(surface: str, morphology: dict | None) -> dict | None:
    """Accept only this occurrence's immediate literal next unit word.

    A whole-line keyword search cannot distinguish the two occurrences in
    '2 번 ... 2 명'. Relative following-token coordinates supplied by the
    existing line analysis provide that distinction. A plain context string
    without occurrence coordinates intentionally remains insufficient.
    """
    if morphology is None or not re.fullmatch(NUMERAL_LITERAL, surface):
        return None
    tokens = morphology.get("following_tokens", [])
    visible = surface + morphology.get("trailing_context", "")
    if not tokens:
        return None
    unit = tokens[0]
    start, end = unit["start"], unit["end"]
    gap = visible[len(surface):start]
    if (start < len(surface) or not gap or not gap.isspace()
            or unit["tag"] not in ("NNB", "NNG")
            or unit["form"] not in NATIVE_COUNTERS.keys() | SINO_UNITS.keys()
            or end <= start or visible[start:end] != unit["form"]):
        return None
    particles = []
    for token in tokens[1:]:
        if token["start"] != end:
            break  # the next word is not part of the unit expression
        if not token["tag"].startswith("J"):
            literal = visible[token["start"]:token["end"]]
            if literal and not any(char.isalnum() for char in literal):
                break  # punctuation ends this word; it is not a suffix
            return None  # lexical continuation, copula, or unverified ending
        if token["end"] <= end or visible[end:token["end"]] != token["form"]:
            return None
        particles.append(deepcopy(token))
        end = token["end"]
    # A truncated or partially tokenized word is not a verified unit boundary.
    if end > len(visible) or (end < len(visible) and re.match(r"[가-힣A-Za-z0-9]", visible[end])):
        return None
    return {"text": unit["form"], "pos": unit["tag"], "context_start": start,
            "context_end": unit["end"], "particle_text": "".join(t["form"] for t in particles),
            "particle_tokens": particles, "analyzer": "Kiwi 0.24.1",
            "source_location": "following_context"}


def _with_following_unit(surface: str, context: str, boundary: dict) -> dict:
    """Borrow a reading context without claiming external glyphs as digits."""
    plan = _number_plan_core(surface + boundary["text"], context)
    plan.update(surface=surface, core_surface=surface, following_unit=deepcopy(boundary))
    choices = []
    for option in plan["options"]:
        parts = deepcopy(option["segments"])
        for part in parts:
            if part["role"] == "unit":
                part.update(source_start=len(surface), source_end=len(surface), exact=False,
                            source_location="following_context", context_start=boundary["context_start"],
                            context_end=boundary["context_end"],
                            context_text=boundary["text"],
                            reason="뒤에 띄어 쓴 단위이며 숫자 원문 범위의 글자가 아닙니다.")
        if boundary["particle_tokens"]:
            particles = boundary["particle_tokens"]
            particle = _segment(boundary["particle_text"], len(surface), len(surface),
                                role="particle", pos="조사", group=parts[-1]["word_group"], exact=False)
            particle.update(query="", tokens=deepcopy(particles), source_location="following_context",
                            context_start=particles[0]["start"], context_end=particles[-1]["end"],
                            context_text=boundary["particle_text"],
                            reason="뒤 단위에 붙은 조사이며 숫자 원문 범위의 글자가 아닙니다.")
            parts.append(particle)
        choices.append(_option(option["id"], option["system"], option["label"], parts,
                               review_reason=option["review_reason"]))
    plan["options"] = choices
    return plan


def number_plan(surface: str, context: str = "", *, morphology: dict | None = None) -> dict:
    """Discover readings with a separately recorded nominal-unit particle.

    A counter such as 2명을 must query 명, not invent a 명을 headword. The
    appended particle is a grammatical alignment record, never a dictionary
    pronunciation of the entire expression.
    """
    following = _following_unit_boundary(surface, morphology)
    if following is not None:
        return _with_following_unit(surface, context, following)
    boundary = _unit_particle_boundary(surface, morphology)
    if boundary is None:
        return _number_plan_core(surface, context)
    core_surface = surface[:boundary["start"]]
    plan = _number_plan_core(core_surface, context)
    plan.update(surface=surface, core_surface=core_surface, particle=boundary)
    choices = []
    for option in plan["options"]:
        parts = option["segments"]
        # A true particle attaches to the unit word; it is not a new spoken
        # numeral and cannot restart the first-syllable length rule.
        particle = _segment(boundary["text"], boundary["start"], boundary["end"],
                            role="particle", pos="조사", group=parts[-1]["word_group"], exact=False)
        particle.update(query="", tokens=deepcopy(boundary["tokens"]))
        choices.append(_option(option["id"], option["system"], option["label"],
                               parts + [particle], review_reason=option["review_reason"]))
    plan["options"] = choices
    return plan


def _number_plan_core(surface: str, context: str = "") -> dict:
    """Discover explicit reading alternatives without choosing ambiguous ones."""
    plan = {"surface": surface, "kind": "unknown", "options": [], "default_option_id": None,
            "selected_option_id": None, "reason": "숫자 표현의 읽기와 단위 범위를 확인해 주세요."}
    options = plan["options"]
    if not surface or len(surface) > 100:
        return plan

    phone = bool(re.fullmatch(r"[0-9]{2,4}(?:-[0-9]{2,4}){2}", surface)) or bool(
        re.search(r"전화|연락처|휴대폰|팩스", context) and re.fullmatch(r"[0-9]{7,15}", surface))
    if phone:
        plan["kind"] = "phone"
        options.append(_digit_option(surface, zero="공", identifier="phone-gong", label="전화번호 · 0을 공으로 읽기"))
        if "0" in surface:
            options.append(_digit_option(surface, zero="영", identifier="phone-yeong", label="전화번호 · 0을 영으로 읽기"))
        plan["reason"] = "전화번호는 0의 읽기와 한 자리씩 끊어 읽는 범위를 선택·검토해야 합니다."
        return plan

    time_match = re.fullmatch(r"([0-9]{1,2}):([0-9]{2})(?::([0-9]{2}))?", surface)
    if time_match:
        plan["kind"] = "time"
        hour, minute, second = time_match.groups()
        if int(hour) <= 23 and int(minute) <= 59 and (second is None or int(second) <= 59):
            minute_start = len(hour) + 1
            tail = _sino_literal(minute, minute_start, 2, padded=True)
            tail += [_unit("분", minute_start + len(minute), 3, inserted=True)]
            if second is not None:
                second_start = len(hour) + len(minute) + 2
                tail += _sino_literal(second, second_start, 4, padded=True)
                tail += [_unit("초", len(surface), 5, inserted=True)]
            hour_readings = []
            if 1 <= int(hour) <= 12:
                hour_native = _native_integer(str(int(hour)), modifier=True)
                # Native contraction and padded hours need separate source
                # alignment review, even if the chosen reading is plausible.
                for part in hour_native:
                    part.update(source_start=0, source_end=len(hour), exact=False)
                hour_readings.append(("time", "mixed", "12시간제 시각으로 읽기", hour_native))
            hour_readings.append(("time-24h", "sino", "24시간제 시각으로 읽기",
                                  _sino_literal(hour, padded=True)))
            for identifier, system, label, hour_parts in hour_readings:
                parts = hour_parts + [_unit("시", len(hour), 1, inserted=True)] + tail
                options.append(_option(identifier, system, label, parts,
                                       review_reason="콜론 표기에는 시·분·초 글자가 없고 낭독 경계가 달라져 원문 숫자 대응을 검토해야 합니다."))
        options.append(_digit_option(surface, identifier="time-digits", label="시각·기록의 숫자를 한 자리씩 읽기"))
        plan["reason"] = "시각·기록 등 콜론의 용법과 시/분 읽기를 확인해 주세요."
        return plan

    date = re.fullmatch(r"([0-9]{1,2})월([0-9]{1,2})일", surface)
    if date:
        plan["kind"] = "date"
        month, day = date.groups()
        if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            if int(month) in (6, 10):
                plan["reason"] = "월 이름의 어형 변화(유월·시월)는 숫자와 발음 음절이 단순 대응하지 않아 직접 근거 확인이 필요합니다."
                return plan
            month_parts = _sino_literal(month, padded=True)
            day_start = len(month) + 1
            day_parts = _sino_literal(day, day_start, 2, padded=True)
            if month_parts and day_parts:
                parts = month_parts + [_unit("월", len(month), 1)] + day_parts + [_unit("일", len(surface) - 1, 3)]
                options.append(_option("date", "sino", "월·일 날짜로 읽기", parts,
                                       review_reason="월·일의 낭독 경계와 실제 날짜 읽기는 개별 확인이 필요합니다."))
        return plan

    ordinal = re.fullmatch(r"제([0-9]+)", surface)
    if ordinal:
        plan["kind"] = "ordinal"
        parts = _sino_integer(ordinal[1], 1)
        if parts:
            prefix = _segment("제", 0, 1, role="prefix", hanja="第", pos="접사", group=0, exact=True)
            options.append(_option("ordinal", "sino", "제…의 순번으로 읽기", [prefix] + parts,
                                   review_reason="접두사 제-를 포함한 한 단어의 장음과 원문 숫자 대응을 확인해야 합니다."))
        return plan

    decimal = re.fullmatch(rf"({NUMERAL_LITERAL})\.([0-9]+)([가-힣%]*)", surface)
    if decimal:
        plan["kind"] = "decimal"
        whole, fraction, suffix = decimal.groups()
        main = _sino_literal(whole)
        if main:
            point = _segment("점", len(whole), len(whole) + 1, role="decimal_point", hanja="點", pos="명사", exact=False)
            for zero in (("영", "공") if "0" in whole + fraction else ("영",)):
                parts = deepcopy(main)
                if whole == "0":
                    parts[0].update(text=zero, query=zero, hanja="零" if zero == "영" else "空")
                parts.append(point)
                for index, digit in enumerate(fraction):
                    text = zero if digit == "0" else SINO[int(digit)]
                    parts.append(_segment(text, len(whole) + 1 + index, len(whole) + 2 + index,
                                          hanja=("零" if zero == "영" else "空") if digit == "0" else HANJA[int(digit)],
                                          pos="명사" if digit == "0" else "수사", exact=True))
                if suffix:
                    unit = "퍼센트" if suffix == "%" else suffix
                    unit_part = _unit(unit, len(whole) + 1 + len(fraction))
                    unit_part["source_end"] = len(surface)
                    unit_part["exact"] = len(unit) == len(suffix) == 1
                    parts.append(unit_part)
                options.append(_option("decimal-" + zero, "sino", "소수로 읽기" + (" · 0은 " + zero if "0" in whole + fraction else ""), parts,
                                       review_reason="소수점·단위의 발음과 낭독 경계를 확인해야 합니다."))
        return plan

    integer = re.fullmatch(rf"({NUMERAL_LITERAL})([가-힣%]*)", surface)
    if not integer:
        return plan
    literal, suffix = integer.groups()
    digits = literal.replace(",", "")
    sino = _sino_literal(literal)
    if sino is None:
        digit_choice = _digit_option(literal, identifier="identifier", label="앞자리 0을 보존하여 한 자리씩 읽기")
        if suffix:
            unit = "퍼센트" if suffix == "%" else suffix
            unit_part = _unit(unit, len(literal))
            unit_part.update(source_end=len(surface), exact=len(unit) == len(suffix) == 1)
            digit_choice = _option("identifier", "digits", digit_choice["label"],
                                   digit_choice["segments"] + [unit_part],
                                   review_reason=digit_choice["review_reason"])
        options.append(digit_choice)
        plan["kind"] = "identifier"
        return plan
    unit = "퍼센트" if suffix == "%" else suffix
    kind = {"년": "year", "월": "date", "일": "date", "원": "money", "%": "percent", "퍼센트": "percent", "번": "ordinal"}.get(suffix, "counter" if suffix else "integer")
    plan["kind"] = kind
    unit_parts = [_unit(unit, len(literal))] if unit else []
    if suffix == "%":
        unit_parts[0].update(source_end=len(surface), exact=False)
    if suffix and unit not in NATIVE_COUNTERS and unit not in SINO_UNITS:
        review = "단위의 수사 결합과 발음을 확인하지 못했습니다."
    else:
        review = ""
    if suffix == "월" and not 1 <= int(digits) <= 12:
        plan["reason"] = "달력의 월 범위를 벗어나 실제 의미를 확인해야 합니다."
        return plan
    # Month-specific allomorphs must not be silently read 육월/십월.
    if suffix == "월" and int(digits) in (6, 10):
        plan["reason"] = "유월·시월의 어형과 숫자 위치 대응을 직접 확인해야 합니다."
        return plan
    options.append(_option("sino", "sino", "한자어 수사로 읽기", sino + unit_parts, review_reason=review))
    native = _native_integer(digits, modifier=bool(suffix))
    if native:
        native_units = [_unit(unit, len(literal), meaning="people")] if suffix == "분" else unit_parts
        options.append(_option("native", "native", "사람을 세는 고유어 수사로 읽기" if suffix == "분" else "고유어 수사로 읽기", native + native_units,
                               review_reason=review or ("이 단위의 고유어 수사 결합은 문맥 확인이 필요합니다." if suffix and suffix not in NATIVE_COUNTERS and suffix not in ("번", "분") else "")))
    elif not suffix or suffix == "번":
        digit_choice = _digit_option(literal, identifier="digits", label="식별 번호를 한 자리씩 읽기")
        if unit:
            digit_choice = _option("digits", "digits", digit_choice["label"], digit_choice["segments"] + unit_parts,
                                   review_reason=digit_choice["review_reason"])
        options.append(digit_choice)
    if suffix in NATIVE_COUNTERS and native and not review:
        plan["default_option_id"] = "native"
        plan["reason"] = "단위 명사에 결합하는 일반적인 고유어 수사 읽기를 제안했습니다. 다른 읽기는 직접 선택할 수 있습니다."
    elif suffix in SINO_UNITS and suffix not in ("번", "시", "분", "월", "일") and not review:
        plan["default_option_id"] = "sino"
        plan["reason"] = "단위에 결합하는 한자어 수사 읽기를 제안했습니다."
    elif suffix == "번":
        # The same line can contain 번호 and 횟수 occurrences of the same
        # written number. Line keywords cannot choose an occurrence's sense.
        plan["reason"] = "횟수(두 번)와 번호(이 번)의 읽기가 다릅니다. 문맥이 분명하지 않으면 선택해 주세요."
    elif suffix == "분":
        plan["reason"] = "시간 단위 분(分)과 사람을 높여 세는 분의 읽기가 다릅니다. 실제 의미를 선택해 주세요."
    elif suffix == "월":
        plan["reason"] = "달력의 월 이름과 기간 단위의 의미 및 실제 단어 경계를 확인해야 합니다."
        for choice in options:
            choice["review_reason"] = "달력 월 이름 전체의 발음 근거와 실제 단어 경계를 확인하지 못했습니다."
    elif suffix == "일":
        plan["reason"] = "달력의 날짜와 기간을 세는 일의 의미를 확인해 실제 읽기를 선택해 주세요."
    elif not suffix:
        plan["reason"] = "한자어 수사·고유어 수사 또는 식별 번호 중 실제 읽기를 선택해 주세요."
    return plan


def _admissible(candidates: list[dict], segment: dict, *, whole=False) -> list[dict]:
    expected = segment.get("pos", "수사")
    hanja = segment.get("hanja", "")
    output = []
    for candidate in candidates:
        if candidate.get("word") != segment["query"]:
            continue
        pos = candidate.get("pos", "")
        if pos != expected:
            # Native multi-part modifiers can have either POS at the complete
            # word level; simple Sino 二 still requires the actual 수사 record.
            if not (whole and segment.get("multipart") and segment.get("role") == "native" and pos in ("수사", "관형사")):
                continue
        origin = candidate.get("hanja") or candidate.get("origin", "")
        if segment.get("origin_policy") == "empty" and origin:
            continue
        if hanja and hanja not in re.sub(r"\s|·|/", "", origin):
            continue
        if segment.get("meaning") == "people" and not re.search(r"사람.*세는.*단위", candidate.get("definition", "")):
            continue
        if segment.get("meaning") == "time" and "시간" not in candidate.get("definition", ""):
            continue
        if expected == "명사" and hanja in ("零", "空") and not ("0" in candidate.get("definition", "") and "수" in candidate.get("definition", "")):
            continue
        if not hanja and segment.get("role") == "native" and origin:
            continue
        output.append(candidate)
    return output


def _unresolved(plan, reason, code="numeric_unverified", *, candidates=None, evidence=None, chosen=None):
    numeric = deepcopy(plan)
    numeric["selected_option_id"] = chosen
    numeric["choice_source"] = "user" if chosen and chosen != plan.get("default_option_id") else "automatic"
    return {"numeric": numeric, "candidates": candidates or [], "evidence_candidates": [],
            "positions": [], "status": "unresolved", "reason": reason, "reason_code": code,
            "evidence_type": None, "pronunciations": [], "pronunciation_scope": "",
            "length_evidence": evidence or [], "applied_rules": [],
            "coverage": {"complete": False, "checked_ranges": [], "unresolved_ranges": [[0, len(plan["surface"])]]}}


async def evaluate_number(plan: dict, client, option_id: str | None = None) -> dict:
    """Verify a chosen reading and project long syllables only through exact maps.

    No caller-supplied pronunciation or positions are accepted. The returned
    character offsets refer to original digits, never a generated Korean string.
    """
    selected = option_id or plan.get("default_option_id")
    option = next((o for o in plan["options"] if o["id"] == selected), None)
    if option is None:
        return _unresolved(plan, plan.get("reason") or "실제 숫자 읽기를 선택해 주세요.", "numeric_reading_required")
    all_candidates = {}
    query_cache = {}
    evidence = []
    direct_positions = []
    rules = []
    groups = {}
    for segment in option["segments"]:
        if segment["role"] == "particle":
            # Grammatical attachment was verified against literal source
            # tokens by number_plan; never query a fabricated inflected unit.
            continue
        groups.setdefault(segment["word_group"], []).append(segment)

    async def lookup(query):
        if query not in query_cache:
            query_cache[query] = await client.lookup(query)
            for candidate in query_cache[query]:
                all_candidates[candidate["id"]] = candidate
        return query_cache[query]

    pending = []
    evidence_ids = []
    actual_readings = []
    any_rule = bool(plan.get("particle") or plan.get("following_unit"))
    if plan.get("particle"):
        boundary = plan["particle"]
        rule = rule_record("6", "원문에서 단위 명사 뒤에만 결합한 조사는 선택한 수사 읽기의 첫음절이나 원문 숫자 위치를 바꾸지 않습니다. 조사 형태를 전체 사전 발음으로 만들지 않습니다.")
        rule.update(id=rule["id"] + "-numeric-particle", label="단위 뒤 조사 결합 · 숫자 장단 범위",
                    source_range=[boundary["start"], boundary["end"]],
                    unit_range=[boundary["unit"]["start"], boundary["unit"]["end"]])
        rules.append(rule)
    if plan.get("following_unit"):
        boundary = plan["following_unit"]
        rule = rule_record("6", "원문 숫자 바로 뒤에 띄어 쓴 단위와 조사 문맥으로 수사의 읽기를 구분합니다. 단위의 원문 밖 좌표를 숫자 글자로 간주하지 않으며 장음은 숫자에 정확히 대응하는 수사 음절만 표시합니다.")
        rule.update(id=rule["id"] + "-numeric-following-unit", label="바로 뒤 단위 문맥 · 숫자 장단 범위",
                    context_range=[boundary["context_start"], boundary["context_end"]],
                    unit=boundary["text"])
        rules.append(rule)
    for group_parts in groups.values():
        query = "".join(p["text"] for p in group_parts)
        first = group_parts[0]
        role = "native" if all(p["role"] == "native" for p in group_parts) else first["role"]
        whole = {**first, "query": query, "role": role,
                 "hanja": "".join(p.get("hanja", "") for p in group_parts),
                 "multipart": len(group_parts) > 1}
        candidates = _admissible(await lookup(query), whole, whole=True)
        agreed = consensus_evidence(candidates, query)
        if candidates:
            if agreed is None:
                pending.append("실제 숫자 읽기의 사전 후보 발음이 상충하거나 없습니다.")
                continue
            group_positions = [first["reading_start"] + p for p in agreed["positions"]]
            direct_positions.extend(group_positions)
            evidence_ids.extend(agreed["evidence_candidates"])
            actual_readings.extend(agreed["pronunciations"])
            evidence.extend({**e, "reading_start": first["reading_start"], "reading_end": group_parts[-1]["reading_end"],
                             "scope": query, "system": option["system"]} for e in agreed["evidence"])
        else:
            # The assembled numeral is not itself listed. Every constituent
            # must be verified, and §6 applies within this single numeral word.
            if len(group_parts) == 1:
                pending.append("숫자·단위 읽기에 맞는 품사·한자의 공식 발음 근거가 없습니다.")
                continue
            group_start = first["reading_start"]
            for part in group_parts:
                candidates = _admissible(await lookup(part["query"]), part)
                agreed = consensus_evidence(candidates, part["query"])
                if agreed is None:
                    pending.append("복합 수사의 구성 요소 발음 근거가 상충하거나 부족합니다.")
                    continue
                local = [part["reading_start"] + p for p in agreed["positions"]]
                kept = [p for p in local if p == group_start]
                direct_positions.extend(kept)
                evidence_ids.extend(agreed["evidence_candidates"])
                actual_readings.extend(agreed["pronunciations"])
                evidence.extend({**e, "reading_start": part["reading_start"], "reading_end": part["reading_end"],
                                 "scope": part["query"], "system": option["system"], "evidence_type": "rule_inference",
                                 "realized_positions": kept, "suppressed_noninitial_positions": [p for p in local if p != group_start]}
                                for e in agreed["evidence"])
            any_rule = True
            rules.append(rule_record("6", "선택한 하나의 복합 수사 안에서는 첫음절의 장음만 연결합니다. 뒤에 나오는 二·四·五 등의 기본형 장음을 무조건 옮기지 않습니다."))
    positions = []
    for reading_position in sorted(set(direct_positions)):
        mappings = [m for m in option["alignment"] if m["reading_start"] <= reading_position < m["reading_end"]]
        precise = [m for m in mappings if m.get("exact") and m["reading_end"] - m["reading_start"] == m["source_end"] - m["source_start"] == 1]
        targets = {m["source_start"] for m in precise if 0 <= m["source_start"] < m["source_end"] <= len(plan["surface"])}
        if len(mappings) != 1 or len(precise) != 1 or len(targets) != 1:
            pending.append("장음 음절과 원문 숫자가 일대일로 대응하지 않아 위치를 추정하지 않습니다.")
        else:
            target = next(iter(targets))
            if not plan["surface"][target].isascii() or not plan["surface"][target].isdigit():
                pending.append("단위·구분 기호의 장음은 원문 숫자 위치로 옮기지 않습니다.")
            else:
                positions.append(target)
    if option.get("review_reason"):
        pending.append(option["review_reason"])
    pronunciation_scope = ("구성 요소별 사전 발음 · 선택한 읽기: " + option["reading"]
                           if any_rule or len(groups) > 1 else option["reading"])
    if pending:
        result = _unresolved(plan, " ".join(dict.fromkeys(pending)), "numeric_alignment" if direct_positions else "numeric_unverified",
                             candidates=list(all_candidates.values()), evidence=evidence, chosen=selected)
        result["numeric"]["choice_source"] = "user" if option_id is not None else "automatic"
        result.update(evidence_candidates=list(dict.fromkeys(evidence_ids)),
                      pronunciations=list(dict.fromkeys(actual_readings)),
                      pronunciation_scope=pronunciation_scope,
                      applied_rules=list({r["id"]: r for r in rules}.values()),
                      evidence_type=("numeric_rule" if any_rule else "numeric_dictionary") if evidence else None)
        return result
    numeric = deepcopy(plan)
    numeric.update(selected_option_id=selected, choice_source="user" if option_id is not None else "automatic")
    return {"numeric": numeric, "candidates": list(all_candidates.values()),
            "evidence_candidates": list(dict.fromkeys(evidence_ids)), "positions": sorted(set(positions)),
            "status": "long" if positions else "none", "reason": "선택한 숫자 읽기의 공식 사전 발음과 원문 숫자 위치를 확인했습니다." + (" 복합 수사의 첫음절 장음 범위는 규칙 판정입니다." if any_rule else ""),
            "reason_code": "numeric_verified", "evidence_type": "numeric_rule" if any_rule else "numeric_dictionary",
            "pronunciations": list(dict.fromkeys(actual_readings)), "pronunciation_scope": pronunciation_scope,
            "length_evidence": evidence, "applied_rules": list({r["id"]: r for r in rules}.values()),
            "coverage": {"complete": True, "checked_ranges": [[0, len(plan["surface"])]], "unresolved_ranges": []}}
