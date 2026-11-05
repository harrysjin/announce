# Render에서 사이트 공개하기

GitHub의 `main` 코드를 Render가 Docker로 빌드하고 웹 화면과 Python API를 함께 실행합니다.
저장소 루트의 `render.yaml`은 **Free 웹 서비스 1개**를 정의합니다.

1. 코드가 GitHub에 올라간 뒤 [Render 배포 화면](https://render.com/deploy?repo=https://github.com/harrysjin/announce)을 엽니다.
2. Render에 로그인하고 연결된 GitHub 계정에서 `harrysjin/announce` 접근을 허용합니다.
   저장소가 보이지 않으면 Render의 GitHub 앱 설치 설정에서 이 저장소를 추가합니다.
   링크로 연결되지 않으면 Dashboard의 **New → Blueprint**에서 해당 저장소와 `main`, `render.yaml`을 선택합니다.
3. 생성 목록이 `sori-studio` 웹 서비스 하나이고 요금제가 **Free**인지 확인합니다.
   Dockerfile의 기본 빌드·시작 명령을 사용합니다. 포트는 Render가 제공하는 `PORT`를 읽습니다.
4. 요청되는 `STDICT_API_KEY` 값에 발급받은 표준국어대사전 인증키를 입력하고 배포를 시작합니다.
   현재 개발 환경의 시크릿은 Render에 자동 복사되지 않습니다. 키를 GitHub 코드나 채팅에 넣지 않습니다.
5. 상태가 **Live**가 되면 Render가 표시하는 `https://…onrender.com` 주소를 엽니다.

`ANALYSIS_CONCURRENCY=1`과 인스턴스 1개를 유지합니다. 컨테이너 안의 Python worker도 1개입니다.
Free 서비스는 유휴 상태에서 중지될 수 있어 첫 접속에 시간이 걸릴 수 있습니다.
PDF와 분석 결과는 임시 저장되며 서버 재시작·재배포 시 사라집니다. 사용자는 결과를 내려받아 보관해야 합니다.

## 공개 주소에서 확인

- `/api/health`가 HTTP 200과 `ok: true`, `dictionary_configured: true`를 반환하는지 확인합니다.
  `dictionary_configured`는 키의 존재 여부이므로 실제 인증은 다음 분석으로 확인합니다.
- 사이트의 예제 PDF를 열어 공식 사전 분석을 실행하고 PDF·검증 기록 다운로드와 작업 삭제를 확인합니다.

## 배포 후 수정

`main`에 변경 사항을 올리면 Render가 자동으로 다시 빌드·배포합니다.
인증키 변경은 Render 서비스의 **Environment**에서 저장하고 재배포합니다.
`sync: false`는 Blueprint를 처음 만들 때 값을 요청하며, 이후 동기화에서는 기존 키를 덮어쓰지 않습니다.

## 설정 검증 범위

필드와 값은 [Render 공식 Blueprint 필드 문서](https://github.com/render-oss/skills/blob/main/skills/render-blueprints/references/field-reference.md)와
[공식 Docker 예제](https://github.com/render-oss/skills/blob/main/skills/render-deploy/assets/docker.yaml)를 대조했습니다.
로컬 YAML 구문과 프로젝트 설정은 검사했으며, Render의 최신 JSON Schema 및 서버 측 검증은 아직 실행하지 않았습니다.
실제 리소스를 만들기 전 Blueprint 생성 화면의 검증 결과를 확인합니다.

## 초기 메모리와 Free 배포 범위

배포 준비 중 앱과 분석기 초기화의 단일 프로세스 최대 RSS를 측정했습니다.
선택 사전을 모두 불러오던 설정은 약565.6MiB였고, 같은 기본 모델을 유지하면서
선택적인 오타·WikiData 구문 사전을 제외한 설정은 약378.3MiB였습니다.
회귀49개와 출처를 대조한27개 표적 평가 결과는 유지됐습니다.

이 값은 초기화·짧은 문장 분석 측정이며 모든20MiB·100페이지 PDF가 Free 메모리 안에서
동작한다는 보장이 아닙니다. Free 구성은 초기 체험용으로 사용하고 실제 Render의
메모리·중단 로그를 확인합니다. 이 설정은 분석을 한 번에1개만 실행합니다.
요금제를 유료로 바꾸는 동작은 자동 수행하지 않습니다.
