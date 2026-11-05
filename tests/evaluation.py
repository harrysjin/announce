"""Small source-audited target evaluation, separate from regression tests.

Default: replay the checked-in official dictionary snapshot without network.
python3 tests/evaluation.py --output test-results/evaluation
Opt in to fresh official requests: add --live (requires STDICT_API_KEY).

This reports precision, recall, decision coverage, and required abstention
separately. It is not an estimate of accuracy on arbitrary scripts.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import fitz

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))

from backend.dictionary import DictionaryClient, DictionaryError
from backend.main import analyze_job
from backend.pdf import extract, output_pdf

FIXTURES = Path(__file__).parent / "fixtures"


class AuditedSnapshot:
    def __init__(self, words: dict):
        self.words = words
        self.queries: list[str] = []
        self.missing_queries: set[str] = set()

    async def lookup(self, word: str):
        self.queries.append(word)
        if word not in self.words:
            self.missing_queries.add(word)
            # Never invent an empty search result for an unaudited lookup.
            raise DictionaryError("평가 snapshot에 없는 검색어이므로 실제 사전 결과를 가정하지 않습니다.")
        return deepcopy(self.words[word])


def make_document(cases: list[dict]) -> bytes:
    document = fitz.open()
    for case in cases:
        page = document.new_page(width=595, height=350)
        page.insert_text((48, 48), case["id"], fontsize=10)
        page.insert_text((48, 112), case["context"], fontname="korea", fontsize=14)
    data = document.tobytes()
    document.close()
    return data


def fraction(numerator: int, denominator: int):
    return round(numerator / denominator, 6) if denominator else None


async def evaluate(corpus_path: Path, output: Path, live: bool = False):
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    cases = corpus["cases"]
    snapshot_path = corpus_path.parent / corpus["snapshot"]
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    dictionary = DictionaryClient() if live else AuditedSnapshot(snapshot)
    original = make_document(cases)
    pages, all_occurrences = extract(original)
    targets = []
    for page_number, case in enumerate(cases, start=1):
        matches = [o for o in all_occurrences if o["page"] == page_number and o["text"] == case["target"]]
        if len(matches) != 1:
            raise AssertionError(f"{case['id']}: target must map to exactly one extracted occurrence; found {len(matches)}")
        targets.append(matches[0])
    job = {
        "id": "source-audited-evaluation", "filename": "소규모_평가자료.pdf",
        "sha256": hashlib.sha256(original).hexdigest(), "pages": pages,
        "occurrences": targets, "marks": [], "settings": {"color": "#CEE4F5", "opacity": .6, "size": 1},
        "analysis": {"state": "running", "done": 0, "total": len({o['text'] for o in targets}), "message": "평가"},
        "history": [], "revision": 0, "warnings": [], "_dictionary": dictionary,
    }
    await analyze_job(job)
    true_positive = false_positive = false_negative = 0
    resolved = correct_resolved = gold_targets = 0
    safety_targets = correct_abstentions = 0
    rows = []
    for case, occurrence in zip(cases, job["occurrences"]):
        glyphs = occurrence["glyph_ids"]
        predicted = sorted({glyphs.index(mark["glyph_id"]) for mark in job["marks"] if mark["glyph_id"] in glyphs and mark["source"] != "manual"})
        expected = case["expected_long_offsets"]
        row = {"id": case["id"], "context": case["context"], "target": case["target"],
               "expected_long_offsets": expected, "predicted_long_offsets": predicted,
               "status": occurrence["status"], "reason_code": occurrence.get("reason_code"), "reason": occurrence["reason"],
               "meaning_status": occurrence.get("meaning_status"), "selected_candidate_id": occurrence.get("selected_candidate_id"),
               "evidence_type": occurrence.get("evidence_type"), "evidence_candidates": occurrence.get("evidence_candidates", []),
               "source": occurrence.get("source"), "audit_evidence": case.get("evidence"), "audit_note": case["audit_note"]}
        if expected is None:
            safety_targets += 1
            correct = occurrence["status"] == "unresolved" and not predicted
            correct_abstentions += correct
            row["required_abstention_respected"] = correct
        else:
            gold_targets += 1
            truth, prediction = set(expected), set(predicted)
            true_positive += len(truth & prediction)
            false_positive += len(prediction - truth)
            false_negative += len(truth - prediction)
            if occurrence["status"] in ("long", "none"):
                resolved += 1
                correct_resolved += prediction == truth
            row["length_correct"] = predicted == expected and occurrence["status"] in ("long", "none")
        rows.append(row)
    metrics = {
        "annotated_targets": len(cases), "dictionary_backed_length_targets": gold_targets,
        "required_abstention_targets": safety_targets,
        "true_positive_syllables": true_positive, "false_positive_syllables": false_positive,
        "false_negative_syllables_including_unresolved": false_negative,
        "syllable_precision": fraction(true_positive, true_positive + false_positive),
        "syllable_recall": fraction(true_positive, true_positive + false_negative),
        "resolved_gold_targets": resolved, "decision_coverage_on_gold_targets": fraction(resolved, gold_targets),
        "correct_resolved_gold_targets": correct_resolved, "decision_accuracy_on_resolved_only": fraction(correct_resolved, resolved),
        "correct_required_abstentions": correct_abstentions,
        "required_abstention_compliance": fraction(correct_abstentions, safety_targets),
        "unscored_context_occurrences": len(all_occurrences) - len(targets),
    }
    report = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "fresh_official_api" if live else "audited_official_snapshot_replay",
        "corpus_title": corpus["title"], "scope": corpus["scope"],
        "annotation_method": corpus["annotation_method"], "metrics_policy": corpus["metrics_policy"],
        "snapshot_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
        "backend_file_sha256": {str(p.relative_to(REPOSITORY)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((REPOSITORY / "backend").glob("*.py"))},
        "analysis": job["analysis"], "metrics": metrics, "cases": rows,
        "missing_snapshot_queries": sorted(getattr(dictionary, "missing_queries", [])),
        "limitations": ["문맥 표적을 선정한 작은 개발 자료이며 독립된 미공개 평가 집합이 아닙니다.",
                        "표적 밖의 문맥 어휘는 채점하지 않았습니다.",
                        "숫자·인명·근거가 없는 결합형은 발음 정답 대신 보류 준수 여부만 평가합니다.",
                        "이 결과를 전체 대본의 정확도나 임의 PDF의 성능으로 해석할 수 없습니다."],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "input.pdf").write_bytes(original)
    (output / "marked.pdf").write_bytes(output_pdf(original, job))
    print(json.dumps({"mode": report["mode"], "analysis": report["analysis"], "metrics": metrics,
                      "missing_snapshot_queries": report["missing_snapshot_queries"]}, ensure_ascii=False, indent=2), flush=True)
    print("Report:", (output / "report.json").resolve(), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=FIXTURES / "audited_corpus.json")
    parser.add_argument("--output", type=Path, default=REPOSITORY / "test-results/evaluation")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Fail on false marks, missing audited queries, or required-abstention violations")
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.corpus, args.output, args.live))
    if args.strict and (report["metrics"]["false_positive_syllables"] or report["missing_snapshot_queries"] or
                        report["metrics"]["correct_required_abstentions"] != report["metrics"]["required_abstention_targets"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
