# 장음 학습 기록 1차 구현

분석 기준: `harrysjin/announce` main `276ac0a6e46acac96a519167e0aeb380bbf18c11`.
기능 브랜치: `feature/learning-audit-phase1`. 운영 배포와 main 병합은 하지 않는다.

## 기존 코드와 보존한 정책

`backend/main.py:history()`는 수정 설명·시각을 최근 500건까지 세션 메모리에 보관하고 revision을 증가시킨다. 이 기능은 그대로 유지한다. `patch_state()`는 원본 glyph ID와 회전 반영 좌표를 검증하고, 복원 시 서버 후보·숫자 결과에서 발음 근거를 재계산한다. `choose_candidate()`는 서버 조회 후보만 허용하고, `choose_reading()`은 서버 숫자 계획의 읽기만 허용한다. 후보 ID는 표제어 target_code와 의미 sense_code를 연결한 `target_code:sense_code`이다.

`src/App.tsx`의 기존 undo/redo는 40개 상태 스냅샷을 보관하고 `/state`에 `restore=true`를 보냈다. 이제 스냅샷마다 사건 ID를 연결하고 `action=undo|redo`, `target_event_id`를 함께 전달한다. 이전 사건을 지우지 않고 상쇄 사건을 추가한다. 취소 대상과 실제 전후 의미·읽기·표시·설정이 일치하는지도 서버에서 확인한다.

`backend/pdf.py`, `backend/linguistics.py`, `backend/numbers.py`, `backend/release.py`, 기존 Render 설정은 변경하지 않는다. 사전 발음과 규칙의 승인 기준을 학습 사례 추천이나 사용자 선택으로 대체하지 않는다. `dictionary.py`의 변경은 시간 초과를 별도 오류 메타데이터로 보존하는 것뿐이다. 기존 판정 상태·오류 설명·재시도 정책은 유지한다.

## 구현 파일

| 파일 | 역할 |
| --- | --- |
| `backend/migrations/001_learning.sql` | 동의·사건·사례·검수 테이블, 무결성 제약, 검색 인덱스 |
| `backend/learning_db.py` | 선택적 PostgreSQL 접속, 타임아웃, 체크섬 기반 마이그레이션 |
| `backend/learning_auth.py` | 익명 소유자 쿠키, 동일 출처 검사, 관리자 Bearer 인증, 문서 그룹 HMAC |
| `backend/learning_events.py` | 최소 문맥·사전 스냅샷, 사건 기록, 중복 방지, 상쇄 검사, 수정 롤백 |
| `backend/learning_api.py` | 동의·본인 삭제·검색·검수·JSONL API |
| `backend/main.py` | 기존 수정 API와 저장 트랜잭션 연결, 저장 중 분석·삭제 경쟁 방지 |
| `src/LearningPanel.tsx` | 학습 관리 화면, 검수·근거·이력·개인정보 관리 |
| `src/learningClient.ts`, `src/types.ts`, `src/App.tsx`, `src/styles.css` | 동의 상태, 요청 ID, 취소 연결, 저장 영수증, 관리 화면 진입 |
| `.env.example`, `requirements.txt` | 비밀 설정 예시와 psycopg 의존성 |
| `tests/test_learning.py` | 동의 미사용, 개인정보 최소화, 근거 분리, 권한·실패 원자성 검사 |
| `tests/test_learning_postgres.py` | 실제 PostgreSQL 전용 통합 검사 14개 |
| `tests/learning_benchmark.py` | 분석 경로 DB 호출 0회 확인 및 선택 저장 지연 측정 |
| `.github/workflows/learning-checks.yml` | 배포 없는 PostgreSQL 16 서비스 기반 CI |

## 데이터베이스 연결과 마이그레이션

별도의 PostgreSQL 데이터베이스를 만들고 서버 비밀 환경변수에 다음 값을 설정한다. 이 작업은 외부 DB를 생성하거나 운영 서버의 비밀 설정을 변경하지 않는다.

| 환경변수 | 설정 |
| --- | --- |
| `DATABASE_URL` | PostgreSQL DSN. 외부 접속은 공급자의 TLS 설정을 따르며 `sslmode=require` 이상을 사용한다. 프런트엔드 환경변수에 넣지 않는다. |
| `LEARNING_DOCUMENT_KEY` | 32자 이상 난수. 원본 SHA-256을 HMAC 처리해 문서 그룹을 만든다. 지속 운영 중에는 동일 키를 보존한다. |
| `LEARNING_ADMIN_TOKEN` | 문서 식별키와 다른 32자 이상 난수. 서버에서만 보관하며 검수자가 관리 화면에서 입력한다. |
| `LEARNING_ADMIN_ID` | 검수자 식별명. 생략하면 토큰 지문의 일부로 식별한다. |
| `LEARNING_PUBLIC_ORIGIN` | HTTPS 리버스 프록시 뒤에서 서비스의 외부 origin을 지정한다. 예: `https://your-service.onrender.com`. Secure 쿠키와 정확한 Origin 검증에 사용한다. |

기존 `STDICT_API_KEY`, `ANALYSIS_CONCURRENCY`는 그대로 사용한다. `.env.example`은 예시이며 서버에서 자동으로 읽지 않는다.

```sh
python -m pip install -r requirements.txt
# 위 비밀값을 서버 프로세스에 주입한 상태에서 실행한다.
python -m backend.learning_db
```

마이그레이션은 명시적으로 실행한다. PostgreSQL advisory transaction lock으로 중복 실행을 직렬화하고, `learning_schema_migrations`에 파일명과 SHA-256을 남긴다. 같은 파일의 반복 적용은 건너뛰고, 적용된 파일이 바뀌면 실패한다. 전체 적용은 단일 트랜잭션이다. 기존 엔진용 테이블이나 Render 설정을 수정하지 않는다.

운영 앱 계정에는 대상 스키마의 테이블 SELECT/INSERT/UPDATE/DELETE만 부여하고, 스키마 변경은 별도 마이그레이션 계정으로 실행하는 구성을 권장한다. 앱 시작·헬스체크에서 DB 연결이나 마이그레이션을 실행하지 않으므로 학습 저장소 장애가 기본 분석 기동을 막지 않는다. 데이터 삭제는 되돌릴 수 없으므로 rollback SQL은 제공하지 않으며, 새 변경은 후속 번호의 마이그레이션으로 추가한다.

## 저장 구조와 개인정보

| 테이블 | 내용 |
| --- | --- |
| `learning_consents` | 브라우저 소유자 해시, 동의 여부·정책 버전·동의/철회 시각 |
| `learning_events` | 고유 UUID, 소유자별 idempotency UUID, 요청 해시, 문서 그룹, 사건 종류, 상쇄 대상, 엔진/빌드 버전, 수정 버전, 언어·시각 변경 |
| `learning_cases` | 사건별 원문·기본형·품사·최대 160자 문맥, 사전 후보·출처·조회 시각·스냅샷 해시, 숫자 읽기·음절 대응, 근거·미확정 사유, 현재 적용 여부, 별도 의미/장음 검수 상태 |
| `learning_reviews` | 검수자, 근거, 검수 전후 상태, 시각 |

원본 PDF, 파일명, 페이지 전체 텍스트, glyph 좌표 배열, 자유 메모는 영구 저장하지 않는다. 짧은 문맥에도 개인정보가 포함될 수 있으므로 동의 안내에 이를 명시한다. 색상·투명도·원형 이동·크기는 사건의 `visual_changes`로 기록하며 언어적 사례를 만들지 않는다. 표시 추가·삭제는 원문 글자 인덱스로 기록한다. 문맥에 같은 단어가 반복되면 실제 glyph의 위치를 기준으로 주변 문맥을 잘라 낸다.

공식 API에 사전 판본 번호가 없으므로 `edition=null`과 그 사유를 보존한다. 후보별 공식 URL·target/sense ID·조회 시각, 당시 후보 내용의 SHA-256, 어댑터/엔진 버전을 남긴다. 이 해시는 사전의 공식 판본 번호를 뜻하지 않는다. 조회 당시의 제공 범위·배제 후보·구성 요소 근거도 구분한다.

동의는 기본 해제다. 브라우저의 HttpOnly/SameSite 쿠키를 해시한 값으로 본인 기록을 구분한다. 쿠키는 HTTPS에서 Secure로 발급한다. 기존 시작 스크립트는 proxy header를 신뢰하지 않으므로 Render처럼 TLS를 종료하는 프록시 뒤에서는 `LEARNING_PUBLIC_ORIGIN`을 외부 HTTPS 주소로 설정해야 한다. 설정이 없을 때도 요청 Host와 Origin 호스트를 비교하므로 기존 프록시 배포의 수정 요청을 내부 HTTP scheme 차이만으로 거부하지 않는다. 원문이나 관리자 토큰을 localStorage에 보관하지 않는다. 로그인 계정과 복구 기능은 1차 범위 밖이므로 쿠키를 지우면 본인 기록에 다시 접근하기 어렵다. 학습 관리 화면에 이 제한을 표시한다.

철회는 신규 수집·검수·승인 사례 내보내기를 중단하지만 본인이 자신의 기존 자료를 확인·내보내기·삭제할 수 있게 한다. 전체 삭제는 동의 행을 삭제하고 FK CASCADE로 해당 사건·사례·검수를 함께 삭제한다. 원본 PDF 임시 작업 삭제와 학습 기록 삭제는 별도 동작이며 UI에 구분해서 안내한다. 서버 원본 PDF는 기존 2시간 만료 정책을 그대로 따른다.

외부로 내보낸 JSONL이나 운영 백업은 API 삭제로 소급 삭제되지 않는다. 운영자는 백업 보존 기한·삭제 반영·접근 통제를 별도로 설정해야 한다. 기본 백그라운드 학습, 익명화의 완전 보장, 영구 데이터의 무기한 활용을 주장하지 않는다.

## 변경 기록과 원자성

동의한 브라우저는 수정에 `X-Learning-Record: true`, `event_id`, `idempotency_key`, `expected_revision`을 보낸다. 서버가 실제 동의 버전과 문서 소유권을 다시 확인한다. 헤더만으로 동의를 대신하지 않는다. 미동의 요청은 PostgreSQL을 호출하지 않고 기존 수정 기능을 이용한다. 동의를 켜거나 끌 때 undo/redo 스택을 초기화하므로 이전 비동의 작업을 소급 수집하지 않는다.

수정은 세션 잠금으로 직렬화한다. DB 동의 행 잠금으로 동의 철회·삭제·검수와 순서를 맞춘다. 요청 해시와 idempotency key가 같으면 기존 사건 영수증을 반환하고 수정을 반복하지 않는다. 같은 키의 다른 요청은 409이다. UUID 재사용, 다른 문서 사건을 취소 대상으로 사용, 같은 사건의 중복 상쇄는 거부한다. 새 수정은 이전 사례를 비활성으로 만들고 새로운 사례를 생성한다.

메모리의 변경 전 상태를 보관하고 PostgreSQL 커밋까지 읽기 응답에는 변경 전 공개 상태를 제공한다. 검증·접속·SQL·커밋 실패 시 메모리 상태도 복원한다. `learning.state=saved`는 커밋 이후에만 반환한다. 응답 유실 시 프런트엔드는 같은 요청·같은 키로 한 번 재시도한다. 서버 재시작으로 임시 PDF 세션은 사라질 수 있지만, 이미 커밋된 학습 기록은 독립 DB에서 계속 조회한다. PDF 작업 복구 기능은 아니다.

DB 연결 대기는 2초, SQL 대기는 1.5초, 잠금 대기는 0.75초로 제한한다. 연결은 수정·관리 요청에서만 생성한다. 숫자 조회 후 저장은 worker thread에서 실행해 이벤트 루프를 DB I/O로 막지 않는다. 기본 분석·진행 조회에는 DB 호출이 없다.

## 검수·인증

사례 상태는 `selected`, `pending_review`, `approved`, `rejected`, `needs_review`이다. 일반 사용자는 본인의 현재 사례를 `selected/needs_review → pending_review`로 제출할 수 있다. 의미와 장음 위치의 상태는 각각 `pending/approved/rejected/needs_review`로 관리한다.

관리자 토큰은 서버에서 constant-time 비교하고, 클라이언트가 보내는 role/reviewer/status 필드로 권한을 부여하지 않는다. UI에서 토큰은 컴포넌트 메모리에만 두며 화면을 닫으면 해제한다. 검수에는 예상 review_version을 요구해 중복·충돌 승인을 막는다. 근거 없는 장음 승인, 이전 상태 사례 승인, 철회한 소유자 사례 검수는 차단한다. 승인·반려·추가 검수의 사유와 서버가 결정한 검수자를 모두 남긴다.

의미가 승인되어도 장음은 별도로 보류할 수 있다. 장음 승인은 서버가 보존한 공식 사전 후보·발음, 필요한 공식 규칙, 전체 검증 범위 및 실제 표시 글자와 엔진 근거 글자의 일치를 요구한다. 사용자가 다른 위치에 표시한 장음은 종전 사전 근거를 가져와 승인할 수 없다. 1차 화면은 새 공식 근거를 추가해 엔진 밖의 수동 사례를 승인하는 편집 기능을 제공하지 않는다. 이런 사례는 추가 검수로 남긴다.

미확정 사유는 `lookup_timeout`, `lookup_failed`, `not_in_dictionary`, `meaning_ambiguous`, `pronunciation_evidence_missing` 등을 구분한다. 전체 분석 제한 시간은 `analysis_error_code=analysis_timeout`으로 별도 보존한다. 원래 판정의 reason_code도 함께 보존하며 알려지지 않은 실패를 임의로 조회 시간 초과로 바꾸지 않는다.

## API와 내보내기

| API | 권한·역할 |
| --- | --- |
| `GET /api/learning/status` | 익명 식별 초기화, 현재 동의·기능 상태 |
| `PUT /api/learning/consent` | 본인, 현재 동의 정책 버전 필요 |
| `GET /api/learning/events` | 본인 수정·상쇄 이력 |
| `GET /api/learning/cases?scope=mine` | 본인 사례·근거·검수 내역, 원문/기본형/문맥 검색과 상태 필터 |
| `POST /api/learning/cases/{id}/submit` | 본인 현재 사례의 검수 요청 |
| `GET /api/learning/admin` | 관리자 토큰 확인 |
| `GET /api/learning/cases?scope=review` | 관리자, 동의 중인 현재 사례 검수 목록 |
| `POST /api/learning/cases/{id}/review` | 관리자, 의미/장음 별도 검수·사유·예상 버전 |
| `GET /api/learning/export.jsonl?scope=mine` | 본인 사건과 연결 사례·검수, 페이지별 최대 500건 |
| `GET /api/learning/export.jsonl?scope=approved` | 관리자, 동의·현재 적용·양쪽 승인 조건을 만족하는 사례 |
| `DELETE /api/learning/records` | 본인 전체 데이터 삭제 및 동의 해제 |

JSONL은 `schema_version=1`, 문서 그룹, 데이터 분리값, 근거 스냅샷을 포함한다. `X-Next-Cursor`가 있으면 `after`로 다음 페이지를 요청해야 하며, UI는 마지막 페이지까지 모아 다운로드한다. 조용히 상위 N건만 내보내지 않는다. 각 페이지는 별도 DB 스냅샷이므로 동시에 편집·검수된 자료의 엄밀한 단일 시점 덤프가 필요한 경우 내보내는 동안 편집을 멈추고 운영 DB에서 일관된 스냅샷을 생성해야 한다.

## 2차·3차 설계만 준비

승인 사례 검색 인덱스는 `(surface, lemma, pos)`와 문맥 tsvector에 마련했다. 향후 후보 검색은 동일 표기만으로 결정하지 않고 기본형·품사·숫자 체계·형태소 경계·주변 문맥을 함께 비교한다. 선택된 의미 ID의 현행 사전 존재와 발음 근거를 다시 확인한다. 반환 화면에는 “유사 문맥의 사용자 검수 사례”로 표시하고 공식 사전 직접 검증과 구분한다. 1차 구현에는 추천 호출·재정렬·자동 확정 활성화 경로가 없다.

JSONL의 `document_group`은 동일 PDF에 대해 사용자와 무관하게 동일하며, 같은 문서가 train/validation/test에 걸치지 않도록 HMAC 값으로 80/10/10 분리값을 부여한다. 반복 문서·근접 복제 문서·동일 대본의 다른 PDF 버전까지 막으려면 향후 텍스트 중복 군집을 문서 그룹 위에 추가한다. 현재 분리값이 독립 시험 데이터의 확보나 실제 재학습을 뜻하지 않는다.

차후 고정 평가 자료와 정책 버전별로 의미 선택 정확도, 장음 음절의 정밀도 TP/(TP+FP), 재현율 TP/(TP+FN), 자동 판정 범위 자동 확정 항목/전체 평가 가능 항목을 측정한다. 미확정 원인별 보류율, 사전 직접/규칙/추천 각각의 성능, 숫자 읽기·활용형·복합어별 성능도 따로 평가한다. 문서 단위 보류 시험과 오탐 허용 기준을 통과하기 전에는 자동 확정 규칙을 추가하지 않는다.

## 검증과 남은 운영 작업

재현 명령:

```sh
npm ci
npm run build
python -m unittest discover -s tests -v
# 테스트 전용 PostgreSQL DSN이 있을 때만 14개 실제 통합 검사를 실행한다.
LEARNING_TEST_DATABASE_URL='<disposable-test-dsn>' python -m unittest discover -s tests -p 'test_learning*.py' -v
PYTHONPATH=.:tests python tests/learning_benchmark.py
```

통합 검사는 지정된 테스트 DB 안에서 무작위 스키마를 생성하고 삭제한다. 운영 `DATABASE_URL`을 테스트 DSN의 대용으로 사용하지 않는다. 마이그레이션 반복, 선택·변경·취소·다시 실행·반복 키, 새 프로세스와 세션 소멸 이후 기록 유지, 철회·삭제·무권한 접근, 검수 근거 부족, JSONL 페이지 처리와 검색을 확인한다. 실제 Render 재배포는 하지 않으며 새 프로세스 검사는 배포의 영구 DB 유지 조건을 모사한다.

2026-11-10 로컬 결과: 기존 158개와 신규 오프라인 13개, 합계 171개 통과. PostgreSQL 전용 14개는 로컬 소켓 생성이 차단되어 미실행. 전체 discover 결과는 185개 중 14개 skip. 프런트엔드 TypeScript/Vite 빌드 통과. Chromium도 실제 실행을 시도했으나 환경에서 socket() 생성이 차단되어 UI 실행 검증은 아직 완료하지 못했다.

합성 숫자 입력을 공식 응답 재생 fixture로 분석한 6회 중앙값: DB 미설정 4.467ms, DB 설정 3.662ms, 두 경로 모두 학습 DB 호출 0회. 모델 초기화 1회는 제외했다. 이 수치는 실시간 공식 API 지연이나 운영 DB 저장 성능을 뜻하지 않으며, DB 설정 경로가 더 빠르다고 해석하지 않는다. 영구 선택 저장의 지연은 실제 PostgreSQL 환경에서 별도 측정해야 한다.

남은 운영 작업은 외부 PostgreSQL 생성·비밀값 설정·마이그레이션 적용, 실제 PostgreSQL 통합 검사와 저장 지연 측정, 브라우저 실행 검수, 운영 백업·보존 기한 설정이다. 이 작업에서는 실제 DB 연결, 비밀값 등록, 운영 배포를 수행하지 않았다. 추천·자동 판정 확장, 계정 복구/다중 관리자 로그인, 엔진 밖 근거 편집은 구현 범위 밖이다.

## 근거

- 분석한 저장소 main: https://github.com/harrysjin/announce/tree/276ac0a6e46acac96a519167e0aeb380bbf18c11
- 원본 선택·복원 정책: `backend/main.py`, `src/App.tsx`; 원문 glyph와 PDF 보존: `backend/pdf.py`.
- PostgreSQL 트랜잭션: https://www.postgresql.org/docs/16/tutorial-transactions.html
- PostgreSQL 행 잠금: https://www.postgresql.org/docs/16/explicit-locking.html
- PostgreSQL 제약: https://www.postgresql.org/docs/16/ddl-constraints.html
- Psycopg 연결·트랜잭션: https://www.psycopg.org/psycopg3/docs/basic/transactions.html
- 표준국어대사전 API: https://stdict.korean.go.kr/openapi/openApiInfo.do
- 판정 근거의 오프라인 재생 자료: `tests/fixtures/dictionary_snapshot_2026-11-04.json`, `tests/fixtures/numeric_dictionary_2026-11-08.json`.
