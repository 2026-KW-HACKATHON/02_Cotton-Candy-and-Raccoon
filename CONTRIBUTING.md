# 🤝 기여 및 협업 가이드

Cotton Candy and Raccoon 팀의 작업 시작부터 리뷰·병합까지 적용하는 공통 규칙입니다. 프로젝트 소개와 기술 스택, 폴더 구조는 [README](README.md)를 참고합니다.

> 이 문서는 팀의 작업 기준입니다. GitHub 브랜치 보호 규칙과 도구 설정은 별도로 적용해야 합니다.

## 🚀 작업 시작

1. 이슈를 만들고 담당자, 작업 영역, 완료 조건을 정합니다.
2. 연동에 필요한 데이터 형식과 선행 작업을 관련 팀원과 확인합니다.
3. 최신 `develop`을 기준으로 작업 브랜치를 생성합니다.
4. 구현과 검증을 마친 뒤 `develop` 대상 PR을 작성하고 리뷰를 요청합니다.

## 🌿 브랜치 & 커밋 전략

### 브랜치 운영 원칙

**Git Flow의 안정 버전과 개발 통합 브랜치 분리 원칙을 해커톤 규모에 맞게 단순화**합니다. `main`, `develop`, 짧게 유지하는 작업 브랜치를 사용합니다.

| 브랜치 | 역할 | 생성 기준 | PR 병합 대상 |
| --- | --- | --- | --- |
| `main` | 시연·제출 가능한 검증된 버전 | 상시 유지 | — |
| `develop` | 모바일·파이프라인·DB 변경 통합 및 연동 확인 | 상시 유지 | `main` |
| `feature/*` | 새 기능 개발 | `develop` | `develop` |
| `fix/*` | 버그 수정 | `develop` | `develop` |
| `refactor/*`, `docs/*`, `test/*`, `chore/*` | 구조 개선·문서·테스트·설정 작업 | `develop` | `develop` |

이 방식을 선택한 이유는 다음과 같습니다.

- **시연 안정성:** 작업 중인 변경은 `develop`에서 확인하고, 시연 가능한 상태만 `main`에 반영합니다.
- **빠른 연동 확인:** 프론트엔드와 백엔드 전용 장기 브랜치를 따로 두지 않고, 작은 작업을 자주 통합해 데이터 형식과 DB 스키마 불일치를 일찍 확인합니다.
- **적은 관리 비용:** 해커톤에서는 여러 버전을 동시에 유지하기보다 하나의 제출 버전을 완성하는 데 집중하므로, 상시 브랜치는 두 개로 제한합니다.
- **작업 추적:** 이슈 번호와 작업 영역을 브랜치에 포함해 담당 변경을 쉽게 찾습니다.

### 브랜치 이름

```text
<type>/<scope>/<issue-number>-<short-description>
```

| Scope | 변경 영역 |
| --- | --- |
| `fe` | `apps/mobile` |
| `be` | `services/pipeline` |
| `db` | `supabase` |
| `common` | 여러 영역에 걸친 작업, 공통 문서·CI |

```text
feature/fe/12-notice-list
feature/be/13-collect-notices
feature/db/14-notice-table
fix/fe/20-empty-notice-screen
docs/common/3-readme
```

- 작업명은 영어 소문자와 하이픈을 사용합니다.
- 하나의 브랜치는 하나의 이슈 또는 밀접하게 연결된 작업을 다룹니다.
- `main`, `develop`에 직접 push하지 않고 PR로 반영합니다.
- 기존 브랜치는 유지하고 새 작업부터 이름 규칙을 적용합니다.
- 작업 브랜치는 병합 후 삭제합니다. `main`, `develop`은 유지합니다.

### 작업 및 병합 흐름

```text
이슈 생성 → develop에서 작업 브랜치 생성 → 구현·검증
         → develop 대상 PR → 리뷰·병합 → 통합 확인
         → develop에서 main으로 PR → 시연 검증·병합
```

### 커밋 메시지

```text
<type>(<scope>): <변경 내용>
```

| Type | 의미 |
| --- | --- |
| `feat` | 기능 추가 |
| `fix` | 버그 수정 |
| `refactor` | 동작을 유지하는 코드 구조 개선 |
| `docs` | 문서 변경 |
| `style` | 코드 포맷·정렬 등 동작에 영향 없는 변경 |
| `test` | 테스트 추가·수정 |
| `chore` | 의존성·빌드·CI·설정 변경 |

```text
feat(fe): 공지 목록 화면 구현
feat(be): 공지 수집 로직 추가
feat(db): 공지 테이블 마이그레이션 추가
fix(fe): 빈 목록 안내 문구 누락 수정
docs(common): 협업 규칙 작성
```

- Scope는 브랜치와 동일하게 `fe`, `be`, `db`, `common`을 사용합니다.
- 브랜치의 `feature`는 커밋에서 `feat`로 표기합니다.
- 제목은 변경 내용을 알 수 있게 작성하며 한글 사용을 허용합니다.
- 한 커밋에는 하나의 목적을 담습니다. 필요하면 본문에 변경 이유와 제약을 적습니다.
- 화면 디자인 변경은 목적에 따라 `feat` 또는 `fix`를 사용합니다. `style`은 코드 서식 변경에만 사용합니다.

## 💬 Issue · PR · 코드 리뷰 전략

### Issue

작업 전에 이슈를 만들고 담당자, 작업 영역, 완료 조건을 정합니다. 프론트엔드·백엔드가 함께 필요한 기능은 작업별 이슈를 연결하고 요청·응답 형식이나 선행 작업을 적습니다.

- 제목: `[FE] 공지 목록 구현`, `[BE] 공지 수집 실패 수정`, `[DB] 공지 테이블 추가`
- 영역 라벨: `fe`, `be`, `db`, `common`
- 유형 라벨: `enhancement`, `bug`, `docs`, `chore`
- 라벨은 저장소에서 생성한 뒤 사용합니다.

양식은 [기능·일반 작업 템플릿](.github/ISSUE_TEMPLATE/feature.md)과 [버그 템플릿](.github/ISSUE_TEMPLATE/bug.md)을 사용합니다.

### Pull Request

- PR 제목은 커밋 규칙과 동일하게 작성합니다. 예: `feat(fe): 공지 목록 화면 구현`
- 하나의 PR은 하나의 목적을 다루며, 리뷰 가능한 크기로 나눕니다.
- 작성자는 자기 변경을 먼저 확인하고, 대상 브랜치와 검증 결과를 명시합니다.
- 일반 작업 PR은 `develop`, 통합·제출 PR은 `main`을 대상으로 합니다.
- 관련 이슈는 `Closes #12`처럼 작성합니다. 기본 브랜치가 `main`이므로 `develop` 병합 시 자동 종료에 의존하지 않고, 통합 동작 확인 후 담당자가 이슈를 닫습니다.

[PR 템플릿](.github/pull_request_template.md)을 사용하고, 변경 영역에 해당하는 체크리스트와 검증 결과를 작성합니다. Issue·PR 양식은 `.github`의 템플릿 파일에서 관리합니다.

### 코드 리뷰 규칙

- **리뷰어 지정**: 변경 영역(Frontend / Mobile, Backend / Pipeline, Database)을 담당하는 팀원을 리뷰어로 지정합니다. API·데이터 형식·DB 스키마 변경은 영향을 받는 파트의 팀원도 함께 확인하고, 공통 문서·CI 변경은 관련 팀원이 리뷰합니다.
- **중점 확인**: 모바일 화면의 로딩·오류·빈 데이터 처리, 파이프라인의 수집 실패·중복 저장 처리, Supabase 스키마와 앱 데이터 형식의 일치 여부를 확인합니다.

코드 리뷰에서는 의견의 중요도와 의도를 명확하게 전달하기 위해 **Pn Rule**을 사용합니다.

- `P1`: 반드시 수정이 필요한 사항
  - 버그, 보안 문제, 크래시 가능성, 명확한 요구사항 위반
- `P2`: 수정이 권장되는 사항
  - 유지보수성, 가독성, 구조 개선, 잠재적인 문제
- `P3`: 선택적으로 반영할 수 있는 제안
  - 코드 스타일, 네이밍, 개인적인 개선 아이디어
- `P4`: 단순 의견 또는 질문
  - 확인이 필요한 부분, 토론 목적의 코멘트

### 병합 기준

- **승인**: 작성자 외 최소 1명 승인 후 병합합니다.
- **피드백 반영**: P1은 반드시 해결한 뒤 병합합니다. P2는 반영을 권장하며 미반영 시 이유를 공유합니다. P3는 선택적으로 반영하고, P4는 필요한 답변이나 논의를 진행합니다. P2~P4 자체는 병합을 막지 않습니다.
- **검증**: 변경 사항의 테스트 결과를 PR에 기록하고, 실행 가능한 CI 검사 통과 및 충돌 여부를 확인합니다. 실행하지 못한 검사는 이유와 대체 확인 결과를 남깁니다.
- **병합 방식**: 작업 브랜치 → `develop`은 Squash merge, `develop` → `main`은 Merge commit을 사용합니다.

## 📐 코드 컨벤션

현재 도구로 설정된 규칙은 프론트엔드의 **TypeScript strict**와 **Expo ESLint**입니다. 아래 나머지 항목은 팀 작성 기준이며, Prettier·Ruff의 상세 설정은 별도 파일로 통일합니다.

### Frontend — Expo / React Native / TypeScript

| 항목 | 규칙 |
| --- | --- |
| 들여쓰기 | 공백 2칸 |
| 컴포넌트·타입 | `PascalCase`: `NoticeCard`, `Notice` |
| 변수·함수 | `camelCase`: `noticeList`, `fetchNotices` |
| 커스텀 Hook | `use` 접두사: `useNotices` |
| 상수 | 모듈 수준의 고정 상수는 `UPPER_SNAKE_CASE` |
| 파일 | 컴포넌트 `NoticeCard.tsx`, Hook `useNotices.ts`, 일반 모듈 `noticeApi.ts` |
| 라우트 파일 | Expo Router 규칙 우선: `index.tsx`, `_layout.tsx`, `[id].tsx` |
| Import | 공통 경로는 설정된 `@/` 별칭 활용 |

- `src/app`은 라우트와 화면 조합을 담당하고, 기능 로직과 UI는 `src/features/<기능>`에 둡니다.
- 여러 기능이 실제로 함께 사용하는 코드만 `src/shared`로 옮깁니다.
- 함수형 컴포넌트와 Hook을 사용하고, 렌더링 중 네트워크 요청이나 상태 변경을 실행하지 않습니다.
- `any` 사용을 피하고 외부 응답은 타입과 실제 데이터 형식을 확인합니다.
- 서버 데이터는 TanStack Query, 전역 클라이언트 상태는 Zustand, 화면 내부 상태는 React 상태로 관리합니다.
- 로딩·오류·빈 데이터 상태를 구분하고, 버튼과 입력 요소에 필요한 접근성 정보를 제공합니다.
- 공통 색상·간격·글꼴 값은 `shared/theme`, 재사용 UI는 `shared/ui`에 모읍니다.
- 공개 가능한 Supabase URL과 publishable key만 모바일 환경 변수에 둡니다. 서버용 비밀 키는 앱에 포함하지 않습니다.

모바일 폴더에서 다음 검사를 실행합니다.

```bash
cd apps/mobile
npm ci
npx tsc --noEmit
npm run lint
```

### Backend — Python pipeline / Supabase

| 항목 | 규칙 |
| --- | --- |
| 들여쓰기 | 공백 4칸 |
| 모듈·함수·변수 | `snake_case`: `notice_parser.py`, `collect_notices` |
| 클래스 | `PascalCase`: `NoticeCollector` |
| 상수 | `UPPER_SNAKE_CASE`: `REQUEST_TIMEOUT` |
| 타입 | 함수 매개변수와 반환값에 타입 힌트 작성 |
| 테스트 | `tests/unit/<영역>/test_<대상>.py`, 함수명 `test_<동작>` |
| DB 식별자 | 테이블·컬럼은 `snake_case` |

- `sources`는 수집, `attachments`는 첨부 처리, `transform`은 변환, `glossary`는 용어 처리, `storage`는 저장소 접근을 담당합니다.
- 네트워크·DB 접근과 순수 데이터 변환을 분리해 테스트할 수 있게 작성합니다.
- 외부 요청에는 타임아웃을 설정하고, 재시도할 오류와 중단할 오류를 구분합니다.
- 예외를 무조건 무시하지 않습니다. 실패한 수집 대상과 원인을 로깅하되 비밀 키·개인 정보는 남기지 않습니다.
- 반복 수집으로 같은 데이터가 중복 저장되지 않도록 고유 키와 저장 정책을 정합니다.
- DB 스키마 변경은 `supabase/migrations`에 새 마이그레이션으로 기록합니다. 이미 공유·적용된 마이그레이션은 수정하지 않습니다.
- 설정과 비밀 값은 환경 변수로 주입하고, 새 변수를 추가하면 용도와 예시를 문서화합니다.
- 프론트엔드에 전달하는 필드명·타입·필수 여부가 바뀌면 관련 문서와 PR에 명시합니다.
- 핵심 변환 로직과 오류 처리는 pytest로 검증하고, 외부 서비스 호출은 테스트 대역을 활용합니다.

#### 파이프라인 테스트 구조와 유지 기준

`services/pipeline/tests`는 다음과 같이 나눕니다.

| 위치 | 용도 |
| --- | --- |
| `tests/unit/<영역>/` | 영역별 단위 테스트. 영역은 `attachments`, `collect`, `easy_text`, `storage`, `summary`, `tooling` |
| `tests/unit/legacy_flow/` | 수집에서 저장까지의 흐름 테스트. e2e 케이스로 대체한 뒤 삭제 |
| `tests/e2e/` | API 응답 예시로 CLI를 실행해 DB 저장과 앱 노출을 기대값과 비교하는 케이스(`cases/`)와 하네스(`harness/`) |
| `tests/support/` | 여러 테스트 파일이 함께 쓰는 helper, fixture, 경로 상수 |
| `tests/fixtures/` | 테스트용 녹화 공지 등 자료 |

- 테스트 파일끼리 서로 import하지 않습니다. 함께 쓰는 helper와 fixture는 `tests/support/`에 두고 `from support.<모듈> import ...`로 가져옵니다.
- 파일 경로는 `Path(__file__)`로 계산하지 않고 `support.paths`의 상수를 사용합니다.
- 새 test 파일은 기존 주제 파일에 넣을 수 없을 때만 만듭니다.

남기는 단위 테스트:

1. 외부 입력 방어: 다운로드 제한, redirect, 형식 서명, HWP 압축 해제 상한, URL 허용 목록, 마스킹 URL 복구
2. 판정 로직: grounding(원문 대조), 일정 역할 검증, 유의사항 누락 검사, 요약 status 결정, 카드 주장 검증, 재요청 병합
3. e2e로 재현하기 어려운 DB 동작: 저장 실패 시 롤백, 동시 실행 토큰 순서, 쉬운말 캐시 경쟁
4. CI 보조 스크립트 검증

지우거나 e2e로 옮기는 테스트:

1. 수집에서 저장까지의 흐름을 확인하는 테스트
2. 다른 테스트와 같은 경로를 반복 확인하는 테스트
3. `_`로 시작하는 내부 함수의 세부 동작만 고정하는 테스트
4. `supabase/tests`와 겹치는 권한 검사

새 기능을 추가할 때는 정상 흐름을 e2e 케이스로, 경계값과 예외를 해당 `unit/` 파일에 추가합니다. 테스트를 삭제할 때는 같은 PR에서 대체 검증 위치를 밝힙니다.

다음은 **파이프라인 프로젝트 설정을 추가한 뒤 사용할** CI 기준 명령입니다. 현재 골격 상태에서는 바로 실행할 수 없습니다.

```bash
cd services/pipeline
uv sync
uv run ruff check
uv run pytest
```

Ruff의 포맷·린트 규칙과 Python 버전은 `pyproject.toml`을 추가할 때 확정하고, 로컬 환경과 CI가 같은 설정을 사용하도록 유지합니다.
