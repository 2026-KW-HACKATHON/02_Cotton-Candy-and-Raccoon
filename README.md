# Cotton Candy and Raccoon

2026 KW 해커톤 팀 **Cotton Candy and Raccoon**의 프로젝트 저장소입니다.
모바일 프론트엔드, 데이터 처리 파이프라인, Supabase 설정을 하나의 저장소에서 관리합니다.

> 기술 스택과 구조는 `develop` 기준입니다. 현재 모바일 앱은 초기 화면, 파이프라인과 기능별 폴더는 골격 단계입니다. 아래 협업 규칙은 팀의 작업 기준이며, GitHub 보호 규칙이나 도구 설정이 모두 적용되어 있다는 의미는 아닙니다.

## 기술 스택

| 영역 | 기술 | 용도 및 현재 상태 |
| --- | --- | --- |
| Frontend | Expo 57, React Native 0.86, React 19, TypeScript 6 | 모바일 앱 기반 |
| Routing | Expo Router | 파일 기반 화면 라우팅 |
| State / Data | Zustand, TanStack Query, Supabase JS | 설치된 의존성, 상태 관리와 데이터 연동에 사용 예정 |
| Mobile 기능 | Expo Notifications, Speech, SQLite, React Native Android Widget | 알림·음성·로컬 저장·위젯용 의존성 |
| Backend pipeline | Python, uv | 수집·가공 파이프라인용 구조와 워크플로 정의, 프로젝트 설정 및 구현 준비 중 |
| Database | Supabase, PostgreSQL 17 | Supabase 로컬 설정 기준, 마이그레이션 폴더 마련 |
| Quality | ESLint, TypeScript strict | 모바일 정적 검사 설정 |
| Test / Format | Jest, Testing Library, Prettier / pytest, Ruff | 모바일은 의존성 등록, 파이프라인은 CI 명령만 정의된 상태 |
| CI / Automation | GitHub Actions | PR 정적 검사 및 공지 수집 워크플로 정의 |

모바일 개발 환경은 `apps/mobile/package.json` 기준 **Node.js 22.13 이상**을 사용합니다. Python 버전과 파이프라인 의존성은 프로젝트 설정 파일을 추가할 때 확정합니다.

## 프로젝트 구조

```text
.
├── .github/
│   └── workflows/
│       ├── ci.yml                  # 모바일·파이프라인 검사 정의
│       └── collect.yml             # 공지 수집 예약·수동 실행 정의
├── apps/
│   └── mobile/
│       ├── assets/images/          # 앱 아이콘·이미지
│       ├── src/
│       │   ├── app/                # Expo Router 라우트와 레이아웃
│       │   ├── features/
│       │   │   ├── glossary/       # 용어 설명
│       │   │   ├── home-widget/    # 홈 위젯
│       │   │   ├── notices/        # 공지
│       │   │   ├── notifications/  # 알림
│       │   │   └── settings/       # 설정
│       │   └── shared/
│       │       ├── accessibility/ # 접근성
│       │       ├── lib/            # 공통 라이브러리·유틸리티
│       │       ├── theme/          # 공통 디자인 값
│       │       ├── types/          # 공통 타입
│       │       └── ui/             # 공통 UI
│       ├── .env.example
│       ├── eslint.config.js
│       ├── tsconfig.json
│       └── package.json
├── services/
│   └── pipeline/
│       ├── src/pipeline/
│       │   ├── attachments/        # 첨부 파일 처리
│       │   ├── glossary/           # 용어 처리
│       │   ├── sources/            # 외부 데이터 수집
│       │   ├── storage/            # 저장소 연동
│       │   └── transform/          # 데이터 변환
│       └── tests/
├── supabase/
│   ├── config.toml                # 로컬 Supabase 설정
│   └── migrations/                # DB 스키마 변경 이력
├── docs/                          # 설계·협업 문서
├── package.json                   # Supabase CLI 개발 의존성
└── README.md
```

기능별 폴더 설명은 각 폴더의 담당 범위를 나타냅니다. `features`, `shared`, 파이프라인 하위 폴더, `migrations`, `docs`는 현재 `.gitkeep`으로 유지하는 골격입니다.

## 브랜치 & 커밋 전략

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

## Issue · PR · 코드 리뷰 전략

### Issue

작업 전에 이슈를 만들고 담당자, 작업 영역, 완료 조건을 정합니다. 프론트엔드·백엔드가 함께 필요한 기능은 작업별 이슈를 연결하고 요청·응답 형식이나 선행 작업을 적습니다.

- 제목: `[FE] 공지 목록 구현`, `[BE] 공지 수집 실패 수정`, `[DB] 공지 테이블 추가`
- 영역 라벨: `fe`, `be`, `db`, `common`
- 유형 라벨: `enhancement`, `bug`, `docs`, `chore`
- 라벨은 저장소에서 생성한 뒤 사용합니다.

**기능·일반 작업 양식**

```markdown
## 🎯 목적
<!-- 이 작업이 필요한 이유와 해결하려는 문제를 작성해주세요. -->

## 🛠️ 작업 내용
<!-- 구현하거나 변경해야 할 작업을 작성해주세요. -->

- [ ] 작업 1
- [ ] 작업 2

## ✅ 완료 조건
<!-- 작업 완료 여부를 확인할 수 있는 기준을 작성해주세요. -->

- [ ] 완료 조건 1
- [ ] 완료 조건 2

## 🔗 의존 작업 및 참고 자료
<!-- 관련 이슈, API/데이터 형식, Figma, 문서 등의 링크를 작성해주세요. -->
```

**버그 양식**

```markdown
## 🐛 문제 설명
<!-- 발생한 문제를 간단히 작성해주세요. -->

## 🔄 재현 방법
<!-- 문제가 발생하는 과정을 순서대로 작성해주세요. -->

1. 실행 환경 및 사전 조건
2. 수행한 동작
3. 발생한 현상

## ✅ 기대 결과
<!-- 정상적으로 동작했을 때 예상되는 결과를 작성해주세요. -->

## ❌ 실제 결과
<!-- 실제로 발생한 결과를 작성해주세요. -->

## 📎 참고 자료
<!-- 로그, 스크린샷, 관련 이슈 등을 첨부해주세요. -->
```

### Pull Request

- PR 제목은 커밋 규칙과 동일하게 작성합니다. 예: `feat(fe): 공지 목록 화면 구현`
- 하나의 PR은 하나의 목적을 다루며, 리뷰 가능한 크기로 나눕니다.
- 작성자는 자기 변경을 먼저 확인하고, 대상 브랜치와 검증 결과를 명시합니다.
- 일반 작업 PR은 `develop`, 통합·제출 PR은 `main`을 대상으로 합니다.
- 관련 이슈는 `Closes #12`처럼 작성합니다. 기본 브랜치가 `main`이므로 `develop` 병합 시 자동 종료에 의존하지 않고, 통합 동작 확인 후 담당자가 이슈를 닫습니다.

**PR 양식**

```markdown
## 📌 작업 유형
- [ ] Frontend / Mobile
- [ ] Backend / Pipeline
- [ ] Database
- [ ] CI / Infra
- [ ] Docs
- [ ] 기타

## 📝 작업 내용
<!-- 무엇을 변경했는지 작성 -->

## 🔗 관련 이슈
Closes #

## ✅ 테스트
<!-- 수행한 테스트 작성 -->

---

## 📱 Frontend / Mobile
> 해당하는 경우에만 작성

- [ ] 실제 기기 / Emulator 동작 확인
- [ ] Android 빌드 확인
- [ ] TypeScript / ESLint 통과
- [ ] UI 변경 시 스크린샷 첨부
- [ ] API 계약 변경 여부 확인

### Screenshot
<!-- UI 변경 시 첨부 -->

---

## 🐍 Backend / Pipeline
> 해당하는 경우에만 작성

- [ ] pytest 통과
- [ ] Ruff 검사 통과
- [ ] DB Schema / Migration 영향 확인
- [ ] API Response 변경 여부 확인
- [ ] 환경변수 추가 여부 확인

---

## 🗄 Database
- [ ] Migration 추가
- [ ] 기존 데이터에 영향 없음
- [ ] Supabase 로컬 환경에서 확인

## ⚠️ 리뷰 시 참고사항
<!-- 리뷰어가 특히 확인해야 할 부분 -->
```

위 양식의 원본은 [기능·일반 작업 템플릿](.github/ISSUE_TEMPLATE/feature.md), [버그 템플릿](.github/ISSUE_TEMPLATE/bug.md), [PR 템플릿](.github/pull_request_template.md)에서 관리합니다. 템플릿을 변경하면 README의 양식도 함께 갱신합니다.

### 코드 리뷰 및 병합 기준

- 작성자 외 **최소 1명 승인**을 받은 뒤 병합합니다. 연동 규격 변경은 영향을 받는 파트의 팀원이 확인합니다.
- 리뷰는 요구사항 충족, 오류·빈 데이터 처리, 데이터 계약, 접근성, 비밀 정보 노출 여부를 중심으로 진행합니다.
- 의견에는 `[필수]`, `[제안]`, `[질문]`을 붙입니다. 필수 수정 사항은 해결하고, 질문에는 답변한 뒤 병합합니다. 단순 선호는 제안으로 구분합니다.
- 작성자는 변경 사항에 맞는 정적 검사·테스트·수동 확인을 수행하고 결과를 PR에 남깁니다.
- 실행 가능한 CI 검사는 통과해야 합니다. 환경 또는 미구현 설정 때문에 실행하지 못한 검사는 성공으로 간주하지 않고, 원인·대체 확인 결과를 적어 리뷰어와 판단합니다.
- 병합 담당자는 승인, 검증 결과, 충돌 여부를 확인합니다.

**현재 CI 상태:** `ci.yml`에는 PR 및 `main` push 시 모바일 TypeScript·ESLint 검사와 파이프라인 `uv sync`·Ruff·pytest 검사가 정의되어 있습니다. 파이프라인은 아직 `pyproject.toml` 및 실행 구현이 없으므로 해당 잡과 공지 수집 워크플로의 정상 실행을 보장하지 않습니다. 설정 완료 후 CI를 필수 검사로 지정합니다.

GitHub의 직접 push 제한, 승인 1명, 필수 검사 강제는 저장소 Ruleset 또는 브랜치 보호 설정에서 별도로 적용합니다.

## 코드 컨벤션

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
| 테스트 | `tests/test_<대상>.py`, 함수명 `test_<동작>` |
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

다음은 **파이프라인 프로젝트 설정을 추가한 뒤 사용할** CI 기준 명령입니다. 현재 골격 상태에서는 바로 실행할 수 없습니다.

```bash
cd services/pipeline
uv sync
uv run ruff check
uv run pytest
```

Ruff의 포맷·린트 규칙과 Python 버전은 `pyproject.toml`을 추가할 때 확정하고, 로컬 환경과 CI가 같은 설정을 사용하도록 유지합니다.
