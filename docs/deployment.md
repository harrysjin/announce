# 공개 배포 준비와 실행

이 문서는 실행 절차이며 실제 서비스 배포나 환경 게시를 수행하지 않습니다.
현재는 단일 Python 서버에서 임시 작업을 처리하는 구조입니다.

## 재현 가능한 준비

저장소 루트에서 Python 3.10+와 Node 20.19+/22.12+를 사용합니다.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
npm ci
npm run build
python3 -m unittest discover -s tests -v
```

서버 시작은 어느 디렉터리에서든 `scripts/start.sh`의 절대 경로로 가능합니다.
기본 포트는 8000이며 호스팅 환경이 `PORT`를 제공하면 해당 포트를 사용합니다.
스크립트는 설치·빌드를 몰래 수행하지 않고, `dist/index.html`이 없으면 종료합니다.

```bash
/workspace/announce/scripts/start.sh
```

## 운영 설정

- Python 실행과 로컬 임시 파일 저장이 가능한 호스팅을 선택합니다.
- 호스팅의 비밀 환경변수에 `STDICT_API_KEY`를 설정합니다. 저장소나 빌드된 브라우저 코드에 키를 넣지 않습니다.
- 외부 HTTPS 목적지 `stdict.korean.go.kr` 접근을 허용하고 기존 프록시·CA·TLS 검증을 유지합니다.
- 프런트엔드와 `/api`를 같은 공개 HTTPS 주소에서 제공합니다. 도메인과 TLS는 호스팅/리버스 프록시에서 연결합니다.
- **ASGI worker는 1개만 사용합니다.** 세션·대기열이 프로세스 메모리에 있어 worker를 여러 개 띄우면 요청에 따라 작업이 사라진 것처럼 보입니다. 다중 인스턴스 확장은 공유 작업 저장소와 큐를 먼저 구현해야 합니다.
- 시작 스크립트는 전달된 프록시 헤더를 신뢰하지 않습니다. 신뢰할 리버스 프록시 주소를 명확히 설정하기 전에는 클라이언트가 보낸 `X-Forwarded-For`를 사용 제한의 기준으로 삼지 않습니다.
- 서버 재시작은 현재 임시 작업을 종료합니다. 작업 보존/복구가 필요한 서비스로 소개하지 않습니다.

## 배포 후 확인

`/api/health`가 200을 반환하는지, HTML·정적 JS/CSS가 정상으로 제공되는지 확인합니다.
`dictionary_configured: true`는 변수 존재 여부이며 실제 사전 인증 성공을 보장하지 않습니다.
공개 주소에서 PDF 업로드 → 공식 사전 분석 → 미확정 검토 → PDF/검증 기록 다운로드 → 작업 삭제를 확인합니다.
실제 사전 시험은 운영 호출을 발생시키므로 기본 회귀 검사와 별도로 수행합니다.

과부하 때는 요청을 대기시키거나 429와 재시도 대기 시간을 안내해야 합니다.
파일 접근·삭제·보관 시간, API 이용 한도, 실제 문서의 처리 시간과 메모리도 공개 전 검증 대상입니다.
기능 범위와 PyMuPDF·사전 이용 조건은 프로젝트 README의 내용을 함께 확인합니다.

## 현재 단일 서버의 작업 제한

현재 기본값은 동시에 분석 2개, 대기 최대 5분, 개별 분석 최대 10분입니다.
대기 중인 작업도 중단할 수 있고, 대기/실행 중에는 표시 수정과 내보내기를 막습니다.
예상하지 못한 오류에서도 확인된 결과를 유지하고 고정된 오류 안내를 반환합니다.
업로드는 연결 주소당 분당 30회, 새 분석 시작은 분당 10회로 제한하며 초과 시
`Retry-After`를 포함한 429를 반환합니다. 이 값은 부하 측정 후 조정할 출발점입니다.
공유 리버스 프록시 뒤에서는 신뢰할 프록시 설정 전까지 같은 주소의 할당량을 공유합니다.
진행 확인용 `/api/jobs/{작업ID}/progress`는 원문·좌표·사전 본문을 보내지 않습니다.
API 응답에는 `Cache-Control: no-store`를 적용합니다.

작업 ID는 파일 접근 권한으로 쓰이므로 운영 시작 스크립트는 URL 접근 로그를 끕니다.
리버스 프록시나 관측 도구에서도 `/api/jobs/` 경로의 작업 ID와 업로드 원문을 기록하지 않도록 설정합니다.
프록시 뒤에서는 주소별 제한이 여러 사용자에게 함께 적용될 수 있으므로 실제 배포 구성을 기준으로 검증해야 합니다.

## 배포 전 한 번에 검사

의존성을 설치한 뒤 저장소 루트에서 실행합니다.

```bash
bash scripts/release-check.sh
```

프런트엔드 빌드와 단위 검사를 마친 뒤 임의의 빈 로컬 포트에서 실제 시작 스크립트를 실행합니다.
상태 확인, HTML·정적 자산, 예제 PDF 응답을 확인하고 해당 서버만 종료합니다.
운영 서버를 띄우거나 공식 사전에 요청하지 않습니다. 키 값도 출력하지 않습니다.
실제 사전 인증과 전체 PDF 분석은 `tests/README.md`의 별도 실연결 검사로 확인합니다.

## Docker로 실행

Dockerfile은 Node 22에서 프런트엔드를 빌드하고 Python 3.12 이미지에 결과와 API를 넣습니다.
Python 의존성은 `requirements.txt`의 고정 버전을 사용하며 Kiwi 모델도 함께 설치합니다.
실행 사용자는 UID/GID 10001이고 하나의 Uvicorn worker가 웹 화면과 API를 함께 제공합니다.
호스팅의 시작 명령은 이미지 기본값을 그대로 사용하고 상태 확인 경로는 `/api/health`로 지정합니다.

일반적인 Docker 환경에서는 다음과 같이 로컬에서 검사할 수 있습니다.
키는 호스팅 비밀 설정이나 현재 셸의 환경변수로 제공하며 명령 인수에 키 값을 쓰지 않습니다.

```bash
docker build -t sori-studio:local .
docker run --rm --name sori-local \
  -p 127.0.0.1:8000:8000 \
  --env STDICT_API_KEY \
  --cap-drop ALL --security-opt no-new-privileges:true \
  sori-studio:local
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다. `PORT`는 기본 8000이며 다른 값을 넣으면
포트 전달과 호스팅의 대상 포트도 함께 맞춥니다. Docker 상태 검사도 같은 `PORT`를 읽습니다.
이미지에는 API 키, `.env`, 원본 PDF, 개발용 의존성 폴더를 복사하지 않습니다.

Compose가 있다면 같은 이미지·포트 설정으로 실행할 수 있습니다.

```bash
docker compose build
docker compose up -d
docker compose ps
docker compose down
```

`SORI_PORT`로 호스트의 로컬 포트를 바꿀 수 있습니다. `STDICT_API_KEY`는 Compose 실행 환경에서
전달됩니다. Compose의 전체 구성 출력에는 치환된 키가 들어갈 수 있으므로 공유하지 않습니다.
로컬 주소에만 포트를 열어 두었으므로 실제 공개 주소와 HTTPS는 호스팅 또는 리버스 프록시에서 연결합니다.
업로드와 결과는 컨테이너의 임시 디렉터리에만 남고 작업 만료·삭제·서버 종료 시 정리됩니다.
컨테이너 교체나 종료로 진행 중인 작업을 복구할 수 없으며 별도 볼륨은 필요하지 않습니다.
한 인스턴스를 유지하고 자동으로 여러 복제본을 만드는 설정은 사용하지 않습니다.

## 이 클라우드 환경에서 Docker를 검사할 때

세션의 프록시와 인증서 신뢰 설정을 유지해야 합니다. Dockerfile의 선택적 BuildKit secret은
빌드 중 npm과 pip가 사용하는 인증서만 전달하며 이미지 계층에 남지 않습니다.

```bash
docker build \
  --secret id=proxy_ca,src="$CODEX_PROXY_CERT" \
  --secret id=system_ca,src=/etc/ssl/certs/ca-certificates.crt \
  -t sori-studio:local .
```

프록시가 있는 이 환경에서 컨테이너의 공식 사전 HTTPS 연결까지 검사하려면 합쳐진 시스템 CA를
읽기 전용으로 마운트하고 Python/httpx가 사용하도록 지정합니다. Docker가 제공하는 프록시 설정을 유지합니다.

```bash
docker run --rm --name sori-local \
  -p 127.0.0.1:8000:8000 \
  --env STDICT_API_KEY \
  --mount type=bind,src=/etc/ssl/certs/ca-certificates.crt,dst=/run/sori-ca.pem,readonly \
  --env SSL_CERT_FILE=/run/sori-ca.pem \
  --cap-drop ALL --security-opt no-new-privileges:true \
  sori-studio:local
```

이 CA는 해당 세션에서만 필요한 설정입니다. 다른 호스팅에서는 그 환경의 정상 CA 신뢰 설정을 사용합니다.
TLS 검증을 끄거나 네트워크 정책을 우회하지 않습니다. 현재 Compose 파일은 일반 호스팅의 기본 설정입니다.
이 관리 환경에서는 위 `docker build`와 CA 마운트가 있는 `docker run` 경로를 사용합니다.
Compose를 사용할 경우에는 빌드용 secret 두 개뿐 아니라 런타임 CA 마운트와 `SSL_CERT_FILE`도
별도 override에 함께 설정해야 합니다.

## 실행 검증 기록 · 2026-11-04 UTC

- 최종 소스의 `scripts/release-check.sh` 통과: 프런트엔드 빌드, 회귀 검사 49개, 실제 시작 스크립트의 상태 확인·HTML·JS/CSS·예제 PDF 응답.
- `docker compose config --quiet` 통과. Compose 서비스 실행 자체는 별도로 수행하지 않았습니다.
- 실제 Docker 이미지 빌드와 UID 10001의 컨테이너 실행을 확인했습니다. Kiwi 0.24.1과 모델 0.24.0의 형태소 분석도 정상 실행했습니다.
- 컨테이너에서 공식 사전 API를 사용한 예제 PDF 분석이 완료됐고 자동 표시 4개를 생성했습니다. PDF·Markdown·CSV 출력, 원문 PDF 보존, 내장 검증 기록, 표시 개수, 작업 삭제 후 404 응답을 확인했습니다.
- 최종 변경을 반영해 이미지를 다시 빌드하고 `PORT=8770`, Docker `healthy`, API `no-store`, 최종 빌드와 동일한 HTML·JS/CSS 바이트, 알 수 없는 API 경로의 404를 확인했습니다. 이 마지막 확인에서 사전 호출을 반복하지 않았습니다.
- 이미지 설정에 API 키가 없고 빌드용 CA secret 파일이 실행 이미지에 남지 않은 것을 확인했습니다. 검증용 컨테이너·작업·이미지 태그를 정리했습니다.

최종 확인 이미지 ID:

```text
sha256:6b20f1e6fe885818ae7560ab1b510627f8ef80e3463158bae51f67fecf010d65
```

이 기록은 로컬 실행·이미지 검증 결과입니다. 공개 서버 배포, 도메인/TLS 연결, 환경 게시는 수행하지 않았습니다.
