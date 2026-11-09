# 숫자 읽기·PDF 렌더링 브라우저 검증

2026-11-09 UTC, `fix/number-reading-pdf-rendering` 작업 트리에서 실행했다.
이 검증은 로컬 Chromium과 loopback 서버만 사용하며 운영 사이트, 실시간 사전 API,
macOS 미리보기는 실행하지 않는다. 일반 대본의 언어 정확도를 추정하는 평가가 아니다.
실행 환경은 Chromium 155.0.8059.79, Playwright 1.62.0, PyMuPDF 1.26.6이다.

## 재현

```sh
npm run build
python3 tests/number_render_browser.py --output test-results/number-render-browser
python3 tests/morphology_browser.py --output test-results/morphology-browser-number-change
```

숫자 서버는 `tests/test_numeric_api.py`의 `NumericReplay`로 사전 클라이언트를 교체하고,
`tests/fixtures/numeric_dictionary_2026-11-08.json`에 저장된 응답만 재생한다.
실제 API 키 대신 UI 설정 상태용 로컬 값을 사용한다. 형태소 회귀도 저장된 응답을 사용한다.
시험 완료 또는 실패 시 각 서버를 종료한다.

키 없는 상태의 기본 기능은 다음 서버와 별도 터미널의 smoke 테스트로 확인한다.

```sh
env -u STDICT_API_KEY python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 9002
```

```sh
python3 tests/browser_smoke.py --base http://127.0.0.1:9002 --output test-results/no-key-browser-number-change
```

## 검증 계약

- `24`의 한자어 읽기를 사용자가 선택하면 규칙 근거로 판정하고 원문의 첫 숫자 `2`에 표시한다.
  읽기 선택 출처, 규칙 판정, 사전 조회 시각, 읽기와 원문 위치 대응을 UI에서 확인한다.
- `2명`은 `[두ː]`, `4개`는 `[네ː]` 근거로 자동 표시하고 `5개`의 `[다섣]`에는 표시하지 않는다.
  `2번`, `10:25`, 전화번호는 읽기와 대응의 검토 상태를 유지한다.
- 사용자 읽기 선택을 실행 취소/다시 실행한 뒤 전체 판정 정보를 비교한다.
- 선택한 `24`의 원을 직접 이동하면 행 배지가 `직접 수정`으로 바뀌고,
  사용자가 고른 읽기와 그 근거는 남는지 확인한다.
- 직접 표시 추가·삭제·삭제 취소·이동, 크기·색상·불투명도 변경과 설정 취소/복원을 실행한다.
- 같은 revision과 배율의 웹 PNG를 내려받은 PDF의 MuPDF 렌더와 바이트 단위로 비교한다.
  SVG 원은 투명한 상호작용 영역인지 확인한다.
- 원문을 불투명한 흰 사각형 위에 작성하여 밑에 그린 표시가 가려지는 경로를 시험한다.
  원문의 모든 비흰색 픽셀을 비교하며 검은 중심뿐 아니라 안티앨리어싱 경계도 보존을 요구한다.
- 모든 원문 글자/좌표, 폰트 정보, 페이지 크기/회전, 기존 링크와 기존 주석을 비교한다.
  PDF에 새 근거 링크와 검증 기록을 추가하는 동작은 허용한다.
- 반복 다운로드, 110% 확대, 새로고침 후 작업 복원과 390px 모바일의 가로 넘침을 검사한다.
  브라우저 JavaScript 오류가 없어야 한다.

시험용 원문의 `example.invalid` 링크와 사각 주석은 원문 보존 검증을 위한 합성 자료다.
공식 사전 근거로 사용하지 않는다. 스크린샷과 PDF는 Git에서 제외된 `test-results/`에 저장한다.

## 실행 결과

| 검사 | 관찰 |
|---|---|
| `npm run build` | 통과 |
| 기존 형태소 브라우저 회귀 | 통과, JavaScript 오류 없음 |
| 키 없는 기본 브라우저 회귀 | 통과, JavaScript 오류 없음 |
| 새 숫자·렌더링 브라우저 회귀 | 최종 렌더러와 UI 수정 후 통과, JavaScript 오류 없음 |

자동/선택 표시, 직접 표시와 설정 변경, 반복 다운로드, 확대, 숫자 원 직접 이동의
다섯 내보내기에서 PNG/PDF 픽셀이 일치했다. 원문 비흰색 픽셀의 변경 수는 모두 **0**이었다.
각 내보내기에서 원문 글자/좌표/폰트/기존 링크/기존 주석도 보존되었다.

검사를 보강할 때 최초 렌더러에서 안티앨리어싱 픽셀 31개가 바뀌는 실패를 먼저 재현했다.
`(252,252,252)`가 `(244,248,250)`으로 바뀌는 사례 등이 있었고 설정 변경 후에는 76개였다.
원문 잉크 검사를 완화하지 않고 보호 영역을 수정한 뒤 동일한 엄격한 검사로 통과했다.

별도로 숫자 읽기를 고른 뒤 원을 옮겼는데도 행 배지가 `읽기 선택`으로 남는 실패도
테스트를 먼저 추가해 재현했다. 최종 실행에서는 `직접 수정` 배지와 기존 읽기 선택 출처의
보존을 모두 확인했다.

최종 숫자 캡처는 `test-results/number-render-browser/`에 있다. `original.pdf`,
`initial-analysis.json`, `numeric-moved-state.json`, 최종 `analysis.json`, 다섯 PDF와 렌더 PNG,
데스크톱·모바일 스크린샷을 저장했다. 기존 형태소와 기본 동작 캡처는 위 재현 명령의 해당
디렉터리에 있다. 중간 진단 실행은 최종 통과 판정에 사용하지 않았다.
