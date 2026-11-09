# 검토용 브랜치와 PR 설명 초안

두 브랜치는 GitHub에 업로드했다. 자동 PR 생성은 `api.github.com/graphql`의 Forbidden으로 실패했으므로 실제 PR이 생성되었다고 주장하지 않는다. main에 병합하거나 Render를 배포하지 않았다.

## 형태소 개선

- Base: `main`
- Head: `fix/long-vowel-morphology`
- 제목: `fix: restore derived lemmas and compound vowel-length evidence`
- [변경 비교·PR 만들기](https://github.com/harrysjin/announce/compare/main...fix/long-vowel-morphology?expand=1)

Kiwi가 명사+XSV/XSA 파생과 복합어를 복원하지 못해 `선발하며` 기본형 및 미등재 구성 요소를 조회하지 못했습니다. 기본형의 품사와 원문 음절 범위를 복원하고 전체 단어의 직접 발음, 좁은 규칙 추론, 부분 확인과 사용자 수정을 구분합니다.

`선발하다`는 先發 단음/選拔 장음이 실제로 상충하므로 뜻을 선택하기 전에는 보류합니다. 직접 등재된 보도부 발음이 구성 요소보다 우선하며, 사회·말의 및 활용·복합어 예외의 보류 정책을 유지합니다.

- 실패 재현 테스트를 먼저 커밋했습니다(5065db1).
- 이 브랜치에서 전체 75개 테스트, 빌드 및 Chromium/PDF 회귀를 통과했습니다.
- 실제 target=5 검색은 이번 질의에서 HTTP200/0건이었습니다. 이를 임의 활용형 생성기로 취급하지 않습니다.
- PDF 원문·좌표와 수정 이력을 보존하고 모든 후보의 출처·시각을 기록합니다.
- 규범 원문 전체 대조, macOS Preview/Adobe Reader 검증은 미완료입니다.

세부 변경과 근거는 docs/morphology-improvement-2026-11-08.md 및 docs/morphology-evidence.md에 있습니다. main과 Render는 변경하지 않았습니다.

## 숫자 읽기·PDF 렌더링

- Base: `fix/long-vowel-morphology`
- Head: `fix/number-reading-pdf-rendering`
- 제목: `feat: verify numeric readings and preserve PDF glyph clarity`
- [변경 비교·PR 만들기](https://github.com/harrysjin/announce/compare/fix/long-vowel-morphology...fix/number-reading-pdf-rendering?expand=1)

아라비아 숫자를 모두 제외하던 분석을 실제 한자어·고유어 읽기 선택과 공식 발음 검증으로 확장합니다. `24`를 이십사로 선택하면 첫 숫자 2만 장음으로 표시하고, 2명/4개는 고유어 두/네의 별도 사전 근거를 사용합니다. 불명확한 읽기나 위치 대응에는 표시하지 않습니다.

PDF는 글자 위 Circle 주석의 Multiply 합성에 의존하던 방식에서 공통 벡터 출력으로 변경했습니다. 원문 뒤에 원을 그리고, 불투명 배경에서는 원문 잉크를 보호하는 클립을 사용합니다. 자동·수동·사용자 수정과 웹 PNG 미리보기가 같은 출력 경로를 공유하며 텍스트·폰트·원본 내용 스트림을 다시 작성하지 않습니다.

- 변경 전에 실패를 재현하고 수정했습니다. 최종 전체 129개 테스트, 빌드, 숫자·형태소·키 없는 브라우저 회귀를 통과했습니다.
- 다섯 웹/PDF 내보내기 비교에서 일치 및 불투명 배경 fixture 원문 잉크 변경 0을 확인했습니다.
- 실제 Chrome PDF 뷰어와 MuPDF에서 검은/색상 글자 내부 보존을 확인했습니다.
- macOS Preview와 Adobe Reader는 미실행입니다. 작은 축소 배율과 복잡한 배경의 클립 한계도 보고서에 기록했습니다.
- 기존 평가 원본은 유지하고 공식 숫자 근거로 갱신한 27개 표적에서 오탐 0, 요구 보류 11/11을 확인했습니다. 일반 정확도 평가가 아닙니다.

이 PR은 fix/long-vowel-morphology 위에 쌓인 변경입니다. docs/number-render-delivery-2026-11-09.md와 docs/pdf-rendering-2026-11-09.md를 참고하세요. main 및 Render 배포는 변경하지 않았습니다.
