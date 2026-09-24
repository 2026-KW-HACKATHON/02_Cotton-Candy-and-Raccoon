# Notice pipeline

Python·uv 기반 공지 수집 파이프라인입니다. 현재는 실행 환경, 공통 모델·설정 검증, 노원구 `NowonNewsNoticeList` API의 공지 한 건 조회와 파일 정보 추출을 구현했습니다. 파일 정보는 API 본문과 해당 공지의 공개 원문 페이지 첨부 목록에서 읽습니다. DB 저장, 월계1동 수집, Actions 예약 수집은 후속 작업입니다.

## 준비

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- PostgreSQL 17(Supabase 로컬 환경 사용 가능, 실제 저장 단계에서 필요)

`uv`는 `.python-version`에 지정된 Python 3.12를 사용합니다.

```powershell
cd services/pipeline
python -m uv sync
```

## 환경 변수

`.env.example`은 변수 이름과 예시만 제공하며 자동으로 로드되지 않습니다. 실제 비밀값은 Git에 커밋하지 말고 실행 쉘이나 GitHub Actions Secrets에서 주입하세요.

PowerShell 예시:

```powershell
$env:DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
$env:SEOUL_API_KEY = "sample"
python -m uv run pipeline check-config
```

| 변수 | 필수 | 용도 |
| --- | --- | --- |
| `DATABASE_URL` | 예 | `psycopg`가 사용할 PostgreSQL 연결 문자열 |
| `SEOUL_API_KEY` | 기존 `check-config`에서만 필수 | 기존 설정값. 현재 노원구 한 건 조회에는 사용하지 않음 |
| `NOWON_NOTICE_API_KEY` | `--source nowon` 명령에서 필수 | 발급받은 노원구 공지 API 키 |
| `HTTP_CONNECT_TIMEOUT_SECONDS` | 아니오 | 연결 타임아웃, 기본값 5초 |
| `HTTP_READ_TIMEOUT_SECONDS` | 아니오 | 응답 읽기 타임아웃, 기본값 20초 |

기존 `check-config`는 필수 변수, DB 주소 형식, 타임아웃을 검사합니다. 성공 시 종료 코드 0,
실패 시 표준 오류(stderr)에 원인을 출력하고 종료 코드 2를 반환합니다.
API 인증이나 DB 연결을 시도하지 않으므로 성공해도 인증키 유효성이나 DB 접근 성공을 의미하지 않습니다.

- `DATABASE_URL`: 호스트가 있는 `postgresql://` 또는 `postgres://` URI를 사용합니다.
  Supabase의 HTTPS 프로젝트 URL이나 `host=...` 형식 DSN은 지원하지 않습니다.
  비밀번호의 특수문자는 URI에 맞게 인코딩해야 합니다.
- `SEOUL_API_KEY`: 기존 설정에서 비어 있지 않은 값을 요구합니다. `sample`도 설정 검사에서는 허용합니다. 노원구 조회 시 자동 대체 키로 사용하지 않습니다.
- `NOWON_NOTICE_API_KEY`: `check-config --source nowon` 및 `collect-one --source nowon`에서 사용합니다. 이 명령에는 `DATABASE_URL`과 `SEOUL_API_KEY`가 필요하지 않습니다. `sample`로 설정 형식 검사는 가능하지만 사용자 발급 키의 유효성은 실제 조회에서 별도 확인해야 합니다.
- 타임아웃: 생략하면 5초/20초이며 유한한 양수만 허용합니다. `0`, 음수, `nan`, `inf`, 빈 값은 거부합니다.
  노원구 API 요청에서 연결·읽기 제한 시간으로 사용합니다.
- 필수 변수의 앞뒤 공백은 제거합니다. 선택 타임아웃의 빈 값은 기본값으로 처리하지 않습니다.

기존 전체 설정은 `Settings.from_env()`, 노원구 조회 설정은 `NowonSettings.from_env()`로 읽습니다. 설정 객체의 출력 표현에서는 DB 주소와 키를 제외합니다.
향후 요청 오류를 출력할 때는 `settings.redact(message)`를 호출하면 알려진 API 키,
DB 연결 URI, 비밀번호와 그 URL 인코딩 표현을 `[REDACTED]`로 바꿉니다.
노원구 수집기는 `httpx` 요청 로그의 URL에 들어갈 수 있는 키를 별도 필터로 가립니다. 다른 라이브러리의 모든 로그나 임의 인코딩까지 자동으로 가리는 기능은 아닙니다.

## 노원구 API에서 공지 한 건 읽기

PowerShell에서 실제 발급 키를 프로세스 환경 변수로 전달합니다. 비밀값을 Git에 저장하지 마세요.

```powershell
$env:NOWON_NOTICE_API_KEY = "발급받은-키"
python -m uv run pipeline check-config --source nowon
python -m uv run pipeline collect-one --source nowon
```

`check-config --source nowon`은 키의 존재와 타임아웃 형식만 검사합니다. 서버 인증은 `collect-one`의 실제 요청에서 확인합니다. 수집 명령은 `/xml/NowonNewsNoticeList/1/1/`의 첫 공지 한 건과 API가 제공한 `LINK`의 공개 원문 페이지를 요청하고, 게시물 번호·제목·등록일·부서·원문 URL·공공누리 유형·본문 HTML 길이와 파일 종류별 개수를 JSON 요약으로 출력합니다. 본문 HTML과 파일명·파일 URL은 콘솔에 출력하지 않으며, DB에는 저장하지 않습니다. 원문 페이지 요청 또는 첨부 목록 파싱이 실패하면 파일 0건으로 간주하지 않고 수집 실패를 반환합니다.

`src/pipeline/attachments/nowon_html.py`의 `extract_files(notice)`는 API `DESCRIPTION`에서 본문 파일을, `extract_page_files(...)`는 원문 페이지의 ‘첨부파일’ 영역에서 별도 첨부를 읽습니다. `sources/nowon_page.py`가 원문 페이지 요청을 맡으며, 노원구 공지 URL·게시물 번호를 검증하고 HTTPS로 요청합니다. `q_fileSn`·`q_fileId`가 모두 있는 `<img src>`는 `inline_image`, 다운로드 `<a href>`는 `attachment`입니다. HTML 파서가 `&amp;`를 처리하고 상대 URL은 절대 URL로 바꿉니다. 같은 파일이 본문과 첨부 목록 양쪽에 있으면 원문 페이지의 첨부 정보를 우선합니다. 파일 자체를 다운로드하거나 PDF·HWP 내용을 분석하지는 않습니다.

현재 DB 고유 제약은 한 공지에서 `file_sn`을 하나만 허용합니다. 하지만 공개 원문 페이지 일부에서는 여러 첨부가 같은 `file_sn`을 공유하고 서로 다른 `file_id`를 사용합니다. 이런 공지는 파일을 임의로 버리지 않고 수집 실패로 처리합니다. 해당 공지의 모든 첨부를 DB에 저장하려면 파일 식별 기준과 DB 고유 제약을 함께 재설계해야 합니다. 이번 변경에서는 DB 스키마를 수정하지 않았습니다.

XML은 인증키를 사용하는 공공 API의 응답 형식이며 RSS가 아닙니다. 실제 JSON 응답은 긴 `ID`를 부동소수점 지수형으로 내보내 끝자리가 달라진 사례가 있어, 문자 그대로 보존되는 XML의 `ID`를 사용합니다. `post_sn`은 문자열로 유지합니다. 월계1동 및 다른 출처도 API와 인증 방식을 확인한 뒤 추가할 계획입니다. 현재 RSS 수집 코드는 없습니다. 월계1동에 적합한 API가 없다면 그 출처의 수집 방식은 따로 결정해야 합니다.

요청은 한 번만 수행하고 리다이렉트를 따라가지 않습니다. 성공은 종료 코드 0, 설정 오류는 2, API·네트워크 오류는 1입니다. 타임아웃·서버 오류는 재시도 가능하다고 알리지만 자동 재시도는 하지 않습니다. 공식 API 주소는 `http://openapi.seoul.go.kr:8088`이며 HTTP 연결이라 전송 구간이 암호화되지 않습니다. 현재 환경에서 해당 서버의 HTTPS 연결 성공은 확인되지 않았습니다. 요청 로그의 키 마스킹은 네트워크 구간 암호화를 대신하지 못합니다.

변수의 정의·사용 위치는 `.env.example`, `src/pipeline/config.py`, `src/pipeline/sources/nowon_api.py`, `src/pipeline/cli.py`입니다. `.github/workflows/collect.yml`에는 아직 새 키와 수집 명령이 연결되지 않았습니다. 이후 DB 저장과 예약 실행을 구현할 때 Repository Secret `NOWON_NOTICE_API_KEY`를 워크플로의 실행 단계 환경 변수로 전달해야 합니다. 실제 Secret 등록 여부는 확인하지 않았습니다.

## 실행과 검증

```powershell
python -m uv run python -m pipeline --help
python -m uv run pipeline check-config
python -m uv run pipeline check-config --source nowon
python -m uv run pipeline collect-one --source nowon
python -m uv run ruff check
python -m uv run pytest
```

공식 설치 프로그램으로 `uv` 실행 파일이 `PATH`에 등록된 환경에서는 위 명령의
`python -m uv`를 `uv`로 줄여 실행할 수 있습니다.
