import type { Candidate } from './types';
import type { LearningSnapshot } from './learningClient';

const evidenceNames: Record<string, string> = {
  dictionary_direct: '사전에 명시된 원문 발음', dictionary_inflection: '사전에 명시된 활용 발음',
  candidate_consensus: '허용 후보의 장단 일치', rule_inference: '공식 근거에 따른 규칙 적용',
  rule_consensus: '허용 후보의 규칙 적용 결과 일치', numeric_rule: '숫자 읽기와 공식 규칙',
  numeric_dictionary: '숫자 읽기의 사전 발음',
};
export const reasonNames: Record<string, string> = {
  resolved: '판정 근거 확인됨', lookup_timeout: '사전 조회 시간 초과', lookup_failed: '사전 연결·응답 확인 필요',
  not_in_dictionary: '사전 미등재', meaning_ambiguous: '문맥에 맞는 뜻 선택 필요',
  pronunciation_evidence_missing: '원문 발음·적용 규칙 근거 부족', not_analyzed: '사전 분석 전',
  manual: '사용자가 직접 수정한 표시', numeric_reading_required: '숫자 읽기 선택 필요',
  numeric_ambiguous: '숫자 읽기 선택 필요', alignment: '읽기와 원문 위치 대응 확인 필요',
  coordinates: 'PDF 글자 위치 확인 필요', partial_evidence: '일부 범위의 발음 근거만 확인됨',
};
export function officialLink(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && (url.hostname === 'korean.go.kr' || url.hostname.endsWith('.korean.go.kr')) ? url.href : undefined;
  } catch { return undefined; }
}
function pronunciation(candidate?: Candidate) {
  return candidate?.pronunciations?.join(' / ') || candidate?.pronunciation || '발음 정보 없음';
}
export function markedPositions(snapshot: LearningSnapshot) {
  const chars = Array.from(snapshot.surface);
  return snapshot.marked_character_indices.map((index) => `${chars[index] || '?'} (${index + 1}번째 글자)`).join(', ') || '표시 없음';
}
export function recordSummary(snapshot: LearningSnapshot) {
  const selected = snapshot.candidates.find((candidate) => candidate.id === snapshot.state.selected_candidate_id);
  const numeric = snapshot.state.numeric;
  const reading = numeric?.options.find((option) => option.id === numeric.selected_option_id);
  return `${selected ? `뜻: ${selected.definition || selected.meaning || '선택됨'}` : reading ? `읽기: ${reading.reading}` : '뜻·읽기 미확정'} · ${markedPositions(snapshot)}`;
}

export default function LearningEvidence({ snapshot }: { snapshot: LearningSnapshot }) {
  const selected = snapshot.candidates.find((candidate) => candidate.id === snapshot.state.selected_candidate_id);
  const numeric = snapshot.state.numeric;
  const reading = numeric?.options.find((option) => option.id === numeric.selected_option_id);
  const rules = snapshot.state.applied_rules || [];
  const actualPronunciation = snapshot.state.pronunciations?.join(' / ');
  return <div className="learning-evidence">
    <dl className="learning-evidence-facts">
      <div><dt>선택한 뜻·읽기</dt><dd>{selected ? <>{selected.word || snapshot.lemma}{selected.hanja && ` (${selected.hanja})`}<span>{selected.definition || selected.meaning || '뜻풀이 미제공'}</span></> : reading ? <>{reading.label}<span>{reading.reading}</span></> : '아직 선택하지 않았습니다.'}</dd></div>
      <div><dt>발음 근거</dt><dd>{actualPronunciation || '원문 발음 미확정'}<span>{snapshot.state.evidence_type ? evidenceNames[snapshot.state.evidence_type] || '적용 범위 재확인 필요' : '공식 발음·규칙의 적용 여부를 확인해야 합니다.'}</span></dd></div>
      <div><dt>표시한 글자</dt><dd>{markedPositions(snapshot)}</dd></div>
      <div><dt>확인 범위</dt><dd>{snapshot.state.coverage?.complete ? '원문 전체 범위 확인' : '전체 범위 미확정'}<span>뜻 선택과 발음·장음 위치는 각각 검수합니다.</span></dd></div>
    </dl>
    {!!rules.length && <div className="learning-rule-list"><h4>적용한 규칙</h4>{rules.map((rule, index) => <p key={`${rule.id}-${index}`}>{officialLink(rule.url) ? <a href={officialLink(rule.url)} target="_blank" rel="noreferrer">{rule.label}</a> : rule.label}{rule.explanation && <span>{rule.explanation}</span>}</p>)}</div>}
    {reading && <div className="learning-numeric-map"><h4>숫자 읽기와 원문 대응</h4><ul>{reading.alignment.map((mapping, index) => {
      const source = mapping.source_location === 'following_context' ? `뒤 문맥 ${mapping.context_text || '단위·조사'}` : mapping.source_start === mapping.source_end ? '원문에 없는 읽기 음절' : `원문 ${Array.from(snapshot.surface).slice(mapping.source_start, mapping.source_end).join('')}`;
      const sound = Array.from(reading.reading).slice(mapping.reading_start, mapping.reading_end).join('');
      return <li key={index}>{source} → {sound || '대응 발음 없음'}<small>{mapping.exact ? '원문 위치 대응 확인' : '장음 표시 위치는 별도 확인'}</small></li>;
    })}</ul></div>}
    {!!snapshot.candidates.length && <div className="learning-candidate-list"><h4>당시 사전 후보 {snapshot.candidates.length}개</h4>{snapshot.candidates.map((candidate) => <article key={candidate.id} className={candidate.id === selected?.id ? 'chosen' : ''}>
      <div><b>{candidate.word || snapshot.lemma}{candidate.hanja && ` (${candidate.hanja})`}</b>{candidate.id === selected?.id && <span className="learning-badge">사용자 선택</span>}</div>
      <p>{candidate.definition || candidate.meaning || '뜻풀이 미제공'}</p><p className="learning-pronunciation">{pronunciation(candidate)} · {candidate.pos || '품사 미제공'}</p>
      <small>{candidate.queried_at ? `조회 ${new Date(candidate.queried_at).toLocaleString()}` : '조회 시각 미기록'}{officialLink(candidate.url) && <> · <a href={officialLink(candidate.url)} target="_blank" rel="noreferrer">표준국어대사전 원문</a></>}</small>
    </article>)}</div>}
    {!snapshot.length_supported && <p className="learning-evidence-hold">의미가 정해져도 공식 발음·규칙과 표시 위치가 확인되기 전에는 장음을 승인하지 않습니다.</p>}
    <details className="learning-raw-record"><summary>상세 원본 기록(JSON)</summary><pre>{JSON.stringify(snapshot, null, 2)}</pre></details>
  </div>;
}
