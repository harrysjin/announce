# Render 운영 반영 상태 — 2026-11-09

사용자가 실제 사이트 반영을 승인하여 검증본 `276ac0a6e46acac96a519167e0aeb380bbf18c11`을
원격 `main`에 fast-forward했다. 강제 푸시나 운영 환경변수 변경은 하지 않았다.
배포 직전 `scripts/release-check.sh`에서 빌드·158개 회귀·실제 시작 스크립트·health·정적 자산·샘플 PDF가 통과했다.

## 배포 시작 경과

GitHub main 반영 직후에는 Render의 새 배포가 시작되지 않았다.
당시 운영 health는 `0.1.0`, 사용자 Deploys 스크린샷도 기존 `25c098b` 한 건의 Live 배포만 표시했다.
사용자가 대시보드에서 Manual Deploy를 실행한 뒤 커밋 `276ac0a`의 Deploy succeeded / Live 화면을 확인했다.
이 환경에는 Render 관리 연결이 없어 대시보드의 버튼을 대신 누를 수 없다.

## 계정에서 필요한 동작

1. Render에서 `sori-studio`의 Deploys를 연다.
2. 오른쪽 위 **Manual Deploy → Deploy latest commit**을 선택한다.
3. 새 배포의 커밋 `276ac0a`와 최종 Live 상태를 확인한다.
4. 사이트를 새로고침한다. 새 health의 `engine_version`은 `2026.11.09.2`, `build_commit`은 위 SHA여야 한다.

자동 배포를 사용하려면 Settings의 Build & Deploy에서 Auto-Deploy와 main 브랜치 설정을 확인한다.
저장소 render.yaml은 main/commit 자동 배포를 선언하지만, 현재 계정의 실제 배포 시작 여부는 별도로 확인해야 한다.

## 배포 후 검사

`tests/production_smoke.py`를 준비하고 로컬 공식 응답 재생 서버에서 통과를 확인했다.
이후 실제 health에서 정확한 커밋을 확인하고 같은 스크립트를 운영 사이트에 실행해 통과했다. 재실행 명령은 다음과 같다.

```sh
python3 tests/production_smoke.py --live \
  --base https://sori-studio.onrender.com \
  --expected-commit 276ac0a6e46acac96a519167e0aeb380bbf18c11 \
  --output test-results/production-2026-11-09
```

생성한 짧은 진단 PDF만 올려 명사/숫자 조사 결합, 실제 사전 근거, 사용자의 뜻·읽기 선택,
화면/PDF 일치와 원문 보존을 검사한다. finally에서 이 검사가 만든 작업만 삭제한다.
다른 작업이나 환경변수·배포 설정은 건드리지 않는다. macOS Preview·Adobe Reader 검사는 별도 미실행이다.

## 실제 운영 검증 완료

2026-11-09 **14:56:57 한국 시각**에 검사를 완료했다.

- 실제 사이트: https://sori-studio.onrender.com
- health: version `0.2.0`, engine `2026.11.09.2`, build `276ac0a6e46acac96a519167e0aeb380bbf18c11`.
- 생성한 PDF 업로드 후 보도부를·2명을·4개를의 첫 글자/숫자 자동 표시 확인.
- 선발하며의 기본형과 상충 후보를 확인하고 選拔 의미 선택 후 첫 선 표시.
- 24의 이십사 읽기 선택 후 첫 2만 표시, reading API HTTP200 및 사용자 선택/규칙 근거 구분 확인.
- 자동 3개 → 선택 후 5개 표시. 원문 글자·glyph 좌표·잉크 보존, 웹 PNG/PDF 픽셀 일치, 모바일 레이아웃 통과.
- JavaScript 오류 0, 외부 출처 링크로의 자동 이동 0.
- 검사에서 만든 작업만 삭제한 뒤 GET404 확인. 사용자 작업은 수정하지 않음.

근거는 `test-results/production-2026-11-09/summary.json`, `initial-analysis.json`,
화면 캡처와 `marked.pdf`에 있다. 실제 배포된 서버의 사전 API를 사용했으며 로컬 fixture를 주입하지 않았다.
이 문서 갱신은 검토용 브랜치에만 저장해 운영 커밋을 불필요하게 다시 배포하지 않았다.
