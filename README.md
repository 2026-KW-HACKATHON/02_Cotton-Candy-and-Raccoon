# Cotton Candy and Raccoon

2026 KW 해커톤 팀 **Cotton Candy and Raccoon**의 프로젝트 저장소입니다.
모바일 프론트엔드, 데이터 처리 파이프라인, Supabase 설정을 하나의 저장소에서 관리합니다.

> 기술 스택과 구조는 `develop` 기준입니다. 현재 모바일 앱은 초기 화면, 파이프라인과 기능별 폴더는 골격 단계입니다.

## 🛠️ 기술 스택

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

## 📁 프로젝트 구조

```text
.
├── .github/
│   ├── ISSUE_TEMPLATE/            # 기능·일반 작업 및 버그 이슈 양식
│   ├── pull_request_template.md   # PR 양식
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
├── CONTRIBUTING.md                # 협업 규칙 및 코드 컨벤션
└── README.md
```

기능별 폴더 설명은 각 폴더의 담당 범위를 나타냅니다. `features`, `shared`, 파이프라인 하위 폴더, `migrations`, `docs`는 현재 `.gitkeep`으로 유지하는 골격입니다.

## 🤝 기여 및 협업

작업 시작 방법, 브랜치·커밋 전략, 코드 컨벤션, PR 작성, 코드 리뷰 및 병합 기준은 [협업 가이드](CONTRIBUTING.md)를 참고해주세요.

- [작업 시작](CONTRIBUTING.md#-작업-시작)
- [브랜치 & 커밋 전략](CONTRIBUTING.md#-브랜치--커밋-전략)
- [Issue · PR · 코드 리뷰 전략](CONTRIBUTING.md#-issue--pr--코드-리뷰-전략)
- [코드 리뷰 규칙](CONTRIBUTING.md#코드-리뷰-규칙)
- [병합 기준](CONTRIBUTING.md#병합-기준)
- [코드 컨벤션](CONTRIBUTING.md#-코드-컨벤션)

### 템플릿

- [기능·일반 작업 이슈 템플릿](.github/ISSUE_TEMPLATE/feature.md)
- [버그 이슈 템플릿](.github/ISSUE_TEMPLATE/bug.md)
- [PR 템플릿](.github/pull_request_template.md)
