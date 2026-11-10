import type { Job } from './types';

export type LearningStatus = {
  available: boolean; consent: boolean; policy_version: string;
  recommendations_enabled: false; auto_confirmation_enabled: false;
};
export type LearningCase = {
  case_id: string; event_id: string; surface: string; lemma: string; pos: string; context: string;
  active: boolean; status: string; meaning_status: string; length_status: string;
  meaning_supported: boolean; length_supported: boolean; unresolved_reason: string;
  review_version: number; created_at: string; snapshot: Record<string, unknown>;
  reviews: { reviewer_id: string; reason: string; reviewed_at: string; after_status: Record<string, unknown> }[];
};
export type LearningEvent = {
  event_id: string; event_type: string; recorded_at: string; compensates_event_id?: string;
  before_revision: number; after_revision: number; changes: unknown[]; visual_changes: unknown;
};
export const LABELS: Record<string, string> = {
  selected: '선택 기록', pending_review: '검수 대기', approved: '승인', rejected: '반려', needs_review: '추가 검수',
  pending: '미확정', meaning_selected: '사전 의미 선택', reading_selected: '숫자 읽기 선택',
  manual_edit: '장음 표시 수정', undo: '실행 취소', redo: '다시 실행', visual_edit: '시각 설정 수정', note_edit: '메모 수정',
};
export async function learningRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : '학습 관리 요청을 처리하지 못했습니다.');
  }
  return response.json();
}
let initialStatus: Promise<LearningStatus> | null = null;
export function initializeLearning() {
  if (!initialStatus) initialStatus = learningRequest<LearningStatus>('/api/learning/status');
  return initialStatus;
}
export function mutationRequest(job: Job, record: boolean, targetEventId?: string | null) {
  return {
    headers: { 'Content-Type': 'application/json', ...(record ? { 'X-Learning-Record': 'true' } : {}) },
    metadata: {
      event_id: crypto.randomUUID(), idempotency_key: crypto.randomUUID(),
      expected_revision: job.revision, target_event_id: record ? targetEventId || null : null,
    },
  };
}
