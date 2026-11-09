# Render 운영 반영 상태 — 2026-11-09

사용자가 실제 사이트 반영을 승인하여 검증본 `276ac0a6e46acac96a519167e0aeb380bbf18c11`을
원격 `main`에 fast-forward했다. 강제 푸시나 운영 환경변수 변경은 하지 않았다.
배포 직전 `scripts/release-check.sh`에서 빌드·158개 회귀·실제 시작 스크립트·health·정적 자산·샘플 PDF가 통과했다.

## 현재 상태

GitHub main 반영은 완료됐으나 Render의 새 배포는 아직 확인되지 않았다.
운영 health는 `0.1.0`, 사용자 Deploys 스크린샷도 기존 `25c098b` 한 건의 Live 배포만 표시했다.
자동 배포가 시작됐다고 가정하거나 사이트 반영 완료로 기록하지 않는다.
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
이것을 실제 운영 검사 성공으로 세지 않는다. 실제 health에서 커밋을 확인한 뒤 다음을 실행한다.

```sh
python3 tests/production_smoke.py --live \
  --base https://sori-studio.onrender.com \
  --expected-commit 276ac0a6e46acac96a519167e0aeb380bbf18c11 \
  --output test-results/production-2026-11-09
```

생성한 짧은 진단 PDF만 올려 명사/숫자 조사 결합, 실제 사전 근거, 사용자의 뜻·읽기 선택,
화면/PDF 일치와 원문 보존을 검사한다. finally에서 이 검사가 만든 작업만 삭제한다.
다른 작업이나 환경변수·배포 설정은 건드리지 않는다. macOS Preview·Adobe Reader 검사는 별도 미실행이다.
