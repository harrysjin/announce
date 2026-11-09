# 검증 범위

`python3 -m unittest discover -s tests -v`

API 통합 검사는 실제 PyMuPDF로 만든 검색 가능한 한국어 PDF를 업로드하고,
출력 PDF를 다시 열어 원문 텍스트·페이지 크기·회전·CropBox·기존 링크·주석을
비교합니다. 사용자 표시와 설정의 실제 출력, 검증 기록 첨부, 글자 좌표,
암호·손상 문서 구분, 혼합 페이지 감지, 작업 삭제도 확인합니다.

인증키가 없는 분석은 `waiting_key`와 미확정 어휘를 유지해야 하며, 사전에서
확인한 것처럼 자동 표시를 만들어서는 안 됩니다.

사전 테스트는 명시적인 합성 fixture와 날짜·출처·조회 시각을 보존한 실제 공식 응답 재생 자료를
구분합니다. 오프라인 재생은 현재 API 접속이나 문맥 판정의 일반적인 정확도를 검증한 결과가 아닙니다. 실제 API 연결에는 서버의
`STDICT_API_KEY`와 해당 목적지 네트워크 접근이 필요합니다.

브라우저 검증은 로컬 Chromium에서 수행하며, macOS 미리보기와 Acrobat Reader는
이 Linux 환경에서 검증하지 않습니다.

## 실제 API 시험 (별도 실행)

서버가 실행 중이고 `STDICT_API_KEY`가 연결된 환경에서만 실행합니다. 실제 조회가
발생하므로 기본 회귀 검사에 자동 포함하지 않습니다. 인증키 값은 출력하지 않습니다.

```bash
# 저장소 루트에서
PYTHONPATH=. python3 tests/live_analysis.py --base http://127.0.0.1:8000
python3 tests/live_browser.py --base http://127.0.0.1:8000
```

첫 명령은 앱의 예시 대본 전체 분석·PDF/MD/CSV·설정 재사용을 확인합니다. 두 번째는
`천천히 / 낭독 / 눈 / 4개 / 0.35초`라는 생성 입력으로 실제 브라우저 자동 표시,
동형어 미확정, 사용자의 사전 의미 선택과 다운로드를 확인합니다. 단어 선정과
기대 상태는 이 실행 시 실제 사전 응답으로 확인했으며 내장 장음 목록으로 사용하지
않습니다. `test-results/`에 결과를 저장하며 원문과 사전 응답을 공개 배포하지 않습니다.
종합 기록은 `docs/live-validation-2026-11-04.md`를 참고하세요.

## 분석기 개선 평가와 운영 검사

`python3 tests/evaluation.py --strict`는 직접 대조한 공식 사전 snapshot과27개 표적의 정답/보류 자료를 재현합니다.
현재 기본값은 숫자 공식 근거를 보충한 `audited_corpus_numbers_2026-11-09.json`입니다.
기존 `4개`·`2026년` 보류를 검증 가능한 장음으로 갱신해 정답16항목·보류11항목을 구분합니다.
이전 `audited_corpus.json`의 정답14/보류13은 원본 그대로 보존하며 새 기능의 정답 분모와 혼동하지 않습니다.
장음 오탐·누락·판정 범위·보류 준수를 별도로 보고합니다.
이 자료는 개발 중 확인한 작은 자료로, 독립적인 일반 정확도 시험이 아닙니다.
`--live`는 실제 사전 재조회를 수행하므로 명시적으로 선택한 경우에만 사용합니다.

`test_evidence.py`는 실제 응답의 활용형·준말 분리, 후보 장단 일치의 출처 보존, 메모 변경과 실행 취소를 검사합니다.
`test_runtime.py`와 `test_runtime_api.py`는 대기/중단/시간 제한,429,경량 진행 응답과 오류 정리를 검사합니다.
`browser_smoke.py`는 사전 키가 없는 별도 서버용 수동 편집 검사이며, 연결된 서버는 `live_browser.py`를 사용하세요.
최신 실행 기록은 `docs/improvements-2026-11-05.md`를 참고하세요.

## 숫자 및 PDF 렌더링 회귀

```sh
python3 tests/number_render_browser.py
python3 tests/morphology_browser.py
python3 tests/pdf_viewer_browser.py
```

첫 두 검사는 Chromium/Playwright로 로컬 웹앱과 다운로드 PDF를 검사합니다.
마지막 검사는 PDF 파일을 Chrome 내장 PDF 뷰어에 직접 열고 MuPDF와 별도로 기록합니다.
Pillow·fonttools와 Noto CJK TTC가 필요하며, 폰트를 내장한 합성 원본을 생성한 뒤 검사합니다.
`--font-collection`, `--chromium`, `--output`으로 설치 경로와 결과 위치를 지정할 수 있습니다.
macOS Preview와 Adobe Reader의 실행 결과를 대신하지 않습니다.

숫자 근거·미확정 범위는 `docs/number-reading-evidence.md`, 렌더링 방식·전후 차이는
`docs/pdf-rendering-2026-11-09.md`, 웹앱 검사 결과는 `docs/number-render-browser-results.md`에 기록합니다.
