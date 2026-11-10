import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, RefreshCw, ShieldCheck, Trash2, X } from 'lucide-react';
import { LABELS, learningRequest, type LearningCase, type LearningEvent, type LearningStatus } from './learningClient';

export default function LearningPanel({ onConsentChange, onClose }: {
  onConsentChange: (enabled: boolean) => void; onClose: () => void;
}) {
  const [status, setStatus] = useState<LearningStatus | null>(null);
  const [cases, setCases] = useState<LearningCase[]>([]);
  const [events, setEvents] = useState<LearningEvent[]>([]);
  const [view, setView] = useState<'mine' | 'review' | 'events'>('mine');
  const [query, setQuery] = useState(''), [state, setState] = useState('');
  const [offset, setOffset] = useState(0), [hasMore, setHasMore] = useState(false);
  const [tokenInput, setTokenInput] = useState(''), [adminToken, setAdminToken] = useState('');
  const [reviewer, setReviewer] = useState('');
  const [error, setError] = useState(''), [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false), [deleteConfirmed, setDeleteConfirmed] = useState(false);
  const loadVersion = useRef(0);
  const adminHeaders = useCallback(() => ({ Authorization: `Bearer ${adminToken}` }), [adminToken]);

  const refreshStatus = useCallback(async () => {
    const next = await learningRequest<LearningStatus>('/api/learning/status');
    setStatus(next);
    onConsentChange(next.consent);
    return next;
  }, [onConsentChange]);
  const load = useCallback(async () => {
    const version = ++loadVersion.current;
    setError('');
    try {
      if (view === 'events') {
        const result = await learningRequest<{ items: LearningEvent[]; has_more: boolean }>(`/api/learning/events?offset=${offset}`);
        if (version === loadVersion.current) { setEvents(result.items); setHasMore(result.has_more); }
      } else {
        const params = new URLSearchParams({ scope: view, q: query, offset: String(offset) });
        if (state) params.set('state', state);
        const result = await learningRequest<{ items: LearningCase[]; has_more: boolean }>(`/api/learning/cases?${params}`, { headers: view === 'review' ? adminHeaders() : {} });
        if (version === loadVersion.current) { setCases(result.items); setHasMore(result.has_more); }
      }
    } catch (failure) { if (version === loadVersion.current) setError((failure as Error).message); }
  }, [view, query, state, offset, adminHeaders]);
  useEffect(() => { void refreshStatus().catch((failure) => setError(failure.message)); }, [refreshStatus]);
  useEffect(() => {
    const timer = setTimeout(() => { if (status?.available) void load(); }, 250);
    return () => { clearTimeout(timer); ++loadVersion.current; };
  }, [status?.available, load]);

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('');
    try { await action(); } catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  async function setConsent(enabled: boolean) {
    if (!status) return;
    await learningRequest('/api/learning/consent', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled, policy_version: status.policy_version }) });
    await refreshStatus(); await load();
    setMessage(enabled ? '이후의 선택·수정 기록을 제공합니다. 이전 작업은 소급 수집하지 않습니다.' : '제공 동의를 철회했습니다. 기존 기록은 검수와 추천 자료에서 제외됩니다.');
  }
  async function download() {
    const parts: string[] = [];
    let cursor = '';
    const seen = new Set<string>();
    do {
      const params = new URLSearchParams({ scope: view === 'review' ? 'approved' : 'mine', limit: '200' });
      if (cursor) params.set('after', cursor);
      const response = await fetch(`/api/learning/export.jsonl?${params}`, { headers: view === 'review' ? adminHeaders() : {} });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || '내보내기에 실패했습니다.');
      }
      parts.push(await response.text());
      cursor = response.headers.get('X-Next-Cursor') || '';
      if (cursor && seen.has(cursor)) throw new Error('내보내기 페이지가 반복되었습니다. 다시 시도해 주세요.');
      seen.add(cursor);
    } while (cursor);
    const url = URL.createObjectURL(new Blob(parts, { type: 'application/x-ndjson' }));
    const link = document.createElement('a'); link.href = url; link.download = 'sori-learning.jsonl'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    setMessage('학습 기록을 JSONL로 내보냈습니다. 문맥에 포함된 개인정보를 확인한 뒤 공유해 주세요.');
  }

  return <section className="learning-panel" aria-label="장음 학습 관리">
    <div className="learning-heading"><div><span className="eyebrow">LEARNING RECORDS</span><h2>장음 학습 관리</h2><p>내가 고른 뜻과 수정 기록을 확인하고, 근거가 확인된 사례를 검수합니다.</p></div><button onClick={onClose} aria-label="작업 공간으로 돌아가기"><X size={20} /></button></div>
    <div className="learning-consent">
      <h3>학습 기록 제공 동의</h3>
      <p>의미·숫자 읽기 선택, 표시 수정, 실행 취소·다시 실행과 시각 설정 변경을 기록합니다. 대상 표현과 최대 160자의 주변 문맥, 사전 후보·출처·조회 시각, 변경 전후 상태를 저장합니다. 메모 내용과 원본 PDF는 저장하지 않습니다.</p>
      <p>미동의 상태에서도 PDF 분석을 이용할 수 있습니다. 철회 시 기존 기록의 검수·추천 활용을 중단하며, 기록 삭제로 학습 데이터와 검수 내역을 모두 지울 수 있습니다. 이 브라우저의 식별 쿠키가 삭제되면 본인 기록에 다시 접근하기 어렵습니다.</p>
      <label><input type="checkbox" checked={!!status?.consent} disabled={!status?.available || busy} onChange={(event) => void run(() => setConsent(event.target.checked))} /> 안내를 확인했으며 이후 수정 기록 제공에 동의합니다.</label>
      {!status?.available && <p role="status">학습 저장소가 아직 연결되지 않았습니다. 작업 공간에서 PDF 분석을 계속 이용할 수 있습니다.</p>}
      <small>동의 정책 {status?.policy_version || '확인 중'} · 문맥 추천 및 자동 판정 확대는 비활성 상태입니다.</small>
    </div>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {message && <p className="learning-message" role="status">{message}</p>}
    <div className="learning-actions">
      <div className="learning-tabs" role="tablist" aria-label="학습 기록 보기">
        {([['mine', '내 학습 사례'], ['events', '수정 이력'], ...(adminToken ? [['review', '검수 목록']] : [])] as [typeof view, string][]).map(([value, label]) => <button role="tab" aria-selected={view === value} key={value} onClick={() => { setView(value); setOffset(0); setCases([]); setEvents([]); }}>{label}</button>)}
      </div>
      <button disabled={busy || !status?.available} onClick={() => void run(load)}><RefreshCw size={15} /> 새로고침</button>
      <button disabled={busy || !status?.available} onClick={() => void run(download)}><ArrowDownToLine size={15} /> {view === 'review' ? '승인 사례' : '내 기록'} JSONL</button>
    </div>
    {view !== 'events' && <form className="learning-search" onSubmit={(event) => { event.preventDefault(); setOffset(0); void run(load); }}>
      <input aria-label="학습 기록 검색" placeholder="원문·기본형·문맥 검색" value={query} onChange={(event) => { setQuery(event.target.value); setOffset(0); }} maxLength={120} />
      <select aria-label="검수 상태" value={state} onChange={(event) => { setState(event.target.value); setOffset(0); }}><option value="">모든 상태</option>{['selected', 'pending_review', 'approved', 'rejected', 'needs_review'].map((value) => <option key={value} value={value}>{LABELS[value]}</option>)}</select>
    </form>}
    <div className="learning-list" aria-busy={busy}>
      {view === 'events' ? events.map((event) => <article className="learning-card" key={event.event_id}><h3>{LABELS[event.event_type] || event.event_type}</h3><p>{new Date(event.recorded_at).toLocaleString()} · 수정 버전 {event.before_revision} → {event.after_revision}</p><small>사건 {event.event_id}{event.compensates_event_id && ` · 상쇄 대상 ${event.compensates_event_id}`}</small><details><summary>변경 전후 근거 보기</summary><pre>{JSON.stringify({ changes: event.changes, visual_changes: event.visual_changes }, null, 2)}</pre></details></article>) : cases.map((item) => <article className="learning-card" key={item.case_id}>
        <div className="learning-case-title"><h3>{item.surface} <small>{item.lemma} · {item.pos || '품사 미확정'}</small></h3><span className={`learning-badge ${item.status}`}>{LABELS[item.status]}{!item.active && ' · 이전 상태'}</span></div>
        <p className="learning-context">{item.context}</p>
        <p>의미: {LABELS[item.meaning_status]} · 발음·장음 위치: {LABELS[item.length_status]}</p>
        <small>미확정 사유: {item.unresolved_reason} · {new Date(item.created_at).toLocaleString()}</small>
        <details><summary>사전 후보·선택·숫자 대응·근거 확인</summary><pre>{JSON.stringify(item.snapshot, null, 2)}</pre></details>
        {!!item.reviews.length && <details><summary>검수 내역 {item.reviews.length}건</summary>{item.reviews.map((review, index) => <p key={index}>{review.reason}<small> · {review.reviewer_id} · {new Date(review.reviewed_at).toLocaleString()}</small></p>)}</details>}
        {view === 'mine' && item.active && status?.consent && ['selected', 'needs_review'].includes(item.status) && <button disabled={busy} onClick={() => void run(async () => { await learningRequest(`/api/learning/cases/${item.case_id}/submit`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expected_version: item.review_version }) }); await load(); })}>검수 요청</button>}
        {view === 'review' && <ReviewForm item={item} disabled={busy} onReview={(meaning, length, reason) => run(async () => { await learningRequest(`/api/learning/cases/${item.case_id}/review`, { method: 'POST', headers: { ...adminHeaders(), 'Content-Type': 'application/json' }, body: JSON.stringify({ meaning_status: meaning, length_status: length, reason, expected_version: item.review_version }) }); await load(); setMessage('검수 상태와 근거를 저장했습니다.'); })} />}
      </article>)}
      {status?.available && !(view === 'events' ? events.length : cases.length) && <p className="learning-empty">표시할 기록이 없습니다.</p>}
    </div>
    <div className="learning-pagination"><button disabled={!offset || busy} onClick={() => setOffset(Math.max(0, offset - 40))}>이전</button><span>{offset + 1}번부터</span><button disabled={!hasMore || busy} onClick={() => setOffset(offset + 40)}>다음</button></div>
    <details className="learning-admin"><summary><ShieldCheck size={16} /> 관리자 인증</summary>{adminToken ? <p>검수자: {reviewer} <button onClick={() => { setAdminToken(''); setReviewer(''); setView('mine'); setCases([]); }}>관리자 로그아웃</button></p> : <form onSubmit={(event) => { event.preventDefault(); void run(async () => { const result = await learningRequest<{ reviewer_id: string }>('/api/learning/admin', { headers: { Authorization: `Bearer ${tokenInput}` } }); setAdminToken(tokenInput); setTokenInput(''); setReviewer(result.reviewer_id); setView('review'); setOffset(0); }); }}><label>관리자 토큰 <input type="password" autoComplete="off" value={tokenInput} onChange={(event) => setTokenInput(event.target.value)} required /></label><button disabled={busy}>인증</button></form>}<p>토큰은 이 화면 메모리에서만 사용되며, 새로고침·화면 닫기 시 해제됩니다.</p></details>
    <div className="learning-privacy"><h3>내 기록 삭제</h3><p>승인·반려 내역과 선택·취소 이력을 모두 삭제하고 제공 동의를 해제합니다. 이미 내려받은 내보내기 파일과 운영자가 관리하는 백업은 별도로 삭제해야 합니다.</p><label><input type="checkbox" checked={deleteConfirmed} onChange={(event) => setDeleteConfirmed(event.target.checked)} /> 내 학습 기록 전체를 삭제하겠습니다.</label><button className="danger" disabled={busy || !deleteConfirmed || !status?.available} onClick={() => void run(async () => { await learningRequest('/api/learning/records', { method: 'DELETE' }); await refreshStatus(); setCases([]); setEvents([]); setDeleteConfirmed(false); setMessage('학습 기록과 검수 내역을 삭제하고 제공 동의를 해제했습니다.'); })}><Trash2 size={15} /> 내 기록 전체 삭제</button></div>
  </section>;
}

function ReviewForm({ item, disabled, onReview }: { item: LearningCase; disabled: boolean; onReview: (meaning: string, length: string, reason: string) => Promise<void> }) {
  const [meaning, setMeaning] = useState(item.meaning_status), [length, setLength] = useState(item.length_status), [reason, setReason] = useState('');
  useEffect(() => { setMeaning(item.meaning_status); setLength(item.length_status); setReason(''); }, [item.review_version, item.meaning_status, item.length_status]);
  return <form className="learning-review-form" onSubmit={(event) => { event.preventDefault(); void onReview(meaning, length, reason); }}>
    <label>의미 확인<select value={meaning} onChange={(event) => setMeaning(event.target.value)}>{['pending', 'approved', 'rejected', 'needs_review'].map((value) => <option key={value} value={value} disabled={value === 'approved' && !item.meaning_supported}>{LABELS[value]}</option>)}</select></label>
    <label>발음·장음 위치 확인<select value={length} onChange={(event) => setLength(event.target.value)}>{['pending', 'approved', 'rejected', 'needs_review'].map((value) => <option key={value} value={value} disabled={value === 'approved' && !item.length_supported}>{LABELS[value]}</option>)}</select></label>
    {!item.length_supported && <p>공식 발음·규칙·표시 위치 근거가 부족하여 장음 승인을 보류합니다.</p>}
    <label>승인·반려·보류 근거<textarea required maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} /></label><button disabled={disabled || !reason.trim()}>검수 저장</button>
  </form>;
}
