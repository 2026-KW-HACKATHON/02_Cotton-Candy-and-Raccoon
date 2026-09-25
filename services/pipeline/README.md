# Notice pipeline

Python·uv 기반 공지 수집 파이프라인입니다. 노원구 `NowonNewsNoticeList` API의 공지 한 건과 공개 원문 페이지의 파일 정보를 수집·변환해 PostgreSQL에 함께 저장합니다. 월계1동 수집과 Actions 예약 수집은 후속 작업입니다.

## 준비

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- 마이그레이션이 적용된 PostgreSQL DB (`collect-one` 저장 시 필요)

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
| `DATABASE_URL` | 예 (`collect-one` 포함) | `psycopg`가 사용할 PostgreSQL 연결 문자열 |
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
- `NOWON_NOTICE_API_KEY`: `check-config --source nowon` 및 `collect-one --source nowon`에서 사용합니다. `check-config --source nowon`에는 DB 정보가 필요 없지만, **DB에 쓰는** `collect-one`에는 `DATABASE_URL`도 필요합니다. `SEOUL_API_KEY`는 두 명령에 쓰지 않습니다. `sample`로 설정 형식 검사는 가능하지만 사용자 발급 키의 유효성은 실제 조회에서 별도 확인해야 합니다.
- 타임아웃: 생략하면 5초/20초이며 유한한 양수만 허용합니다. `0`, 음수, `nan`, `inf`, 빈 값은 거부합니다.
  노원구 API 요청에서 연결·읽기 제한 시간으로 사용합니다.
- 필수 변수의 앞뒤 공백은 제거합니다. 선택 타임아웃의 빈 값은 기본값으로 처리하지 않습니다.

기존 전체 설정은 `Settings.from_env()`, 노원구 조회 설정은 `NowonSettings.from_env()`로 읽습니다. 설정 객체의 출력 표현에서는 DB 주소와 키를 제외합니다.
향후 요청 오류를 출력할 때는 `settings.redact(message)`를 호출하면 알려진 API 키,
DB 연결 URI, 비밀번호와 그 URL 인코딩 표현을 `[REDACTED]`로 바꿉니다.
노원구 수집기는 `httpx` 요청 로그의 URL에 들어갈 수 있는 키를 별도 필터로 가립니다. 다른 라이브러리의 모든 로그나 임의 인코딩까지 자동으로 가리는 기능은 아닙니다.

## 노원구 공지 한 건 수집·저장

PowerShell에서 실제 발급 키를 프로세스 환경 변수로 전달합니다. 비밀값을 Git에 저장하지 마세요.

```powershell
$env:NOWON_NOTICE_API_KEY = "발급받은-키"
$env:DATABASE_URL = "postgresql://사용자:비밀번호@127.0.0.1:54322/postgres"
python -m uv run pipeline check-config --source nowon
python -m uv run pipeline collect-one --source nowon
```

`check-config --source nowon`은 키의 존재와 타임아웃 형식만 검사합니다. 서버 인증과 DB 연결은 `collect-one`에서 실제로 확인합니다. **`collect-one`은 DB를 변경하므로 처음에는 전용 테스트 DB에서 실행하세요.** 명령은 `/xml/NowonNewsNoticeList/1/1/`의 첫 공지와 공개 원문 페이지를 요청하고, 수집·변환이 모두 성공한 뒤 공지와 파일 목록을 한 트랜잭션으로 저장합니다. 반환된 `notice_id`, 게시물 번호, 제목, 날짜, 파일 종류별 개수 등을 JSON으로 출력합니다. 본문 HTML과 파일명·파일 URL은 콘솔에 출력하지 않습니다. 원문 페이지 요청 또는 첨부 목록 파싱이 실패하면 파일 0건으로 간주하지 않고 DB 저장 전에 종료합니다.

`src/pipeline/attachments/nowon_html.py`의 `extract_files(notice)`는 API `DESCRIPTION`에서 본문 파일을, `extract_page_files(...)`는 원문 페이지의 ‘첨부파일’ 영역에서 별도 첨부를 읽습니다. `sources/nowon_page.py`가 원문 페이지 요청을 맡으며, 노원구 공지 URL·게시물 번호를 검증하고 HTTPS로 요청합니다. `q_fileSn`·`q_fileId`가 모두 있는 `<img src>`는 `inline_image`, 다운로드 `<a href>`는 `attachment`입니다. HTML 파서가 `&amp;`를 처리하고 상대 URL은 절대 URL로 바꿉니다. 같은 `(file_id, kind)`가 본문과 첨부 목록 양쪽에 있으면 원문 페이지의 첨부 정보를 우선하고, 같은 파일이 첨부와 본문 이미지 두 역할로 등장하면 각각 보존합니다. 파일 자체를 다운로드하거나 PDF·HWP 내용을 분석하지는 않습니다.

DB 마이그레이션의 파일 고유 제약은 `(notice_id, file_id, kind)`입니다. `file_sn`은 원본 메타데이터로 보존하며 같은 번호의 서로 다른 파일을 허용합니다. 한 입력에서 같은 `(file_id, kind)`가 서로 다른 URL로 반복되면 임의로 하나를 고르지 않고 수집 실패로 처리합니다. 전체 수집이 성공했을 때만 파일 목록을 DB에 전달합니다.

`transform/nowon.py`의 `transform_nowon_notice(notice)`는 API의 `RawNotice`를 DB 저장용 `NoticeRecord`로 바꿉니다. 노원구 출처·공공누리 유형을 확인하고, 등록일을 `date`로 변환하며, 원문 페이지 조회와 동일한 URL 규칙으로 HTTPS 주소를 만듭니다. 텍스트도 이미지·링크도 없는 본문만 `None`으로 바꾸고, 이미지나 링크만 있는 본문 HTML은 그대로 보존합니다. 변환 함수는 네트워크나 DB에 접근하지 않으며 `collect-one`에서 저장 직전에 호출합니다.

## 공지 단독 저장 함수

`storage/notices.py`의 `save_notice(conn, record)`는 변환된 `NoticeRecord`를 `notices`에 `(category, post_sn)` 기준으로 한 SQL 문에서 저장·갱신하고 DB `id`를 반환합니다. 새 공지는 `is_modified=False`, `is_visible=True`로 시작합니다. 기존 공지는 제목·본문 HTML·등록일·원문 URL·공공누리 유형 중 하나라도 이전 값과 다르면 `is_modified=True`가 되고, 이후 원래 값으로 돌아와도 `True`를 유지합니다. 부서는 최신 값으로 갱신하지만 부서만 변경되면 수정됨으로 표시하지 않습니다. 다시 수집한 공지는 `is_visible=True`로 복원하고 `updated_at`을 갱신하며 `created_at`은 유지합니다. 동일값 비교에는 SQL의 `IS DISTINCT FROM`을 사용해 `NULL` 변경도 감지합니다.

함수는 `commit`, `rollback`, 연결 종료를 하지 않습니다. `DatabaseSettings.from_env()`는 DB 연결에 필요한 `DATABASE_URL`만 읽어 검증하므로 API 키 없이도 사용할 수 있습니다. `psycopg.connect(settings.database_url)`로 연결한 뒤 변환된 레코드를 함수에 전달합니다. `collect-one`은 아래의 공지·파일 묶음 저장 함수를 사용합니다.

## 공지와 파일 함께 저장

`storage/notice_bundle.py`의 `save_notice_with_files(conn, notice, files)`는 완전히 수집·변환된 공지와 파일 목록을 **공지 한 건 단위의 트랜잭션**으로 저장하고 `notices.id`를 반환합니다. 새 공지는 고유 키 `(category, post_sn)`의 `INSERT ... ON CONFLICT DO NOTHING RETURNING id` 결과로 구별하며 첫 파일 저장을 수정으로 표시하지 않습니다. 기존 공지는 7단계 upsert로 갱신합니다. 파일은 `(file_id, kind)`별로 `file_sn`, `file_name`, `url`을 비교하므로 입력 순서만 바뀌면 DB 파일 행과 `is_modified`를 그대로 둡니다. 파일 정보가 실제로 달라졌을 때만 기존 목록을 삭제·재삽입하고 기존 공지의 `is_modified=True`로 유지합니다. 파일 입력의 `(category, post_sn)`이 공지와 다르거나 같은 파일 키의 정보가 충돌하면 저장 전에 거부합니다.

`files=[]`는 **본문과 원문 페이지를 정상적으로 수집했는데 파일이 없는 경우**에만 전달해야 합니다. 페이지 요청·파싱이 실패한 경우에는 이 함수를 호출하지 않아야 기존 파일을 잘못 삭제하지 않습니다. 파일 INSERT 등 트랜잭션 내부 오류는 공지 변경까지 롤백합니다. 함수가 독립 트랜잭션으로 실행되면 정상 종료 시 확정되며, 호출자가 이미 트랜잭션을 열었다면 내부 작업은 savepoint로 묶여 바깥 트랜잭션에 남습니다. `collect-one`은 수집·변환 성공 후 DB 연결을 열고 이 함수를 호출합니다.

실제 PostgreSQL 통합 테스트는 기존 마이그레이션이 적용된 **테스트용** DB에 `PIPELINE_TEST_DATABASE_URL`을 설정한 뒤 실행할 수 있습니다. 테스트는 고유한 게시물 번호를 사용합니다. 저장 계층 테스트는 종료 시 트랜잭션을 롤백하고 CLI 통합 테스트는 확정된 해당 테스트 행만 삭제합니다. 이 변수를 설정하지 않으면 DB 통합 테스트 17개를 건너뛰므로, 건너뛴 상태를 저장 검증 완료로 해석하면 안 됩니다. 2026-09-26에 별도 임시 PostgreSQL 17 DB에서 전체 **327개 테스트를 건너뛰기 없이 통과**시켰고, 발급 키로 실제 공지를 두 번 저장해 중복이 없는 것도 확인했습니다. 확인 후 임시 서버·데이터를 삭제했습니다.

XML은 인증키를 사용하는 공공 API의 응답 형식이며 RSS가 아닙니다. 실제 JSON 응답은 긴 `ID`를 부동소수점 지수형으로 내보내 끝자리가 달라진 사례가 있어, 문자 그대로 보존되는 XML의 `ID`를 사용합니다. `post_sn`은 문자열로 유지합니다. 월계1동 및 다른 출처도 API와 인증 방식을 확인한 뒤 추가할 계획입니다. 현재 RSS 수집 코드는 없습니다. 월계1동에 적합한 API가 없다면 그 출처의 수집 방식은 따로 결정해야 합니다.

요청은 한 번만 수행하고 리다이렉트를 따라가지 않습니다. 성공은 종료 코드 0, 설정 오류는 2, 수집·변환·DB 오류는 1입니다. 타임아웃·서버 오류는 재시도 가능하다고 알리지만 자동 재시도는 하지 않습니다. 공식 API 주소는 `http://openapi.seoul.go.kr:8088`이며 HTTP 연결이라 전송 구간이 암호화되지 않습니다. 현재 환경에서 해당 서버의 HTTPS 연결 성공은 확인되지 않았습니다. 요청 로그의 키 마스킹은 네트워크 구간 암호화를 대신하지 못합니다.

변수의 정의·사용 위치는 `.env.example`, `src/pipeline/config.py`, `src/pipeline/sources/nowon_api.py`, `src/pipeline/cli.py`입니다. `.github/workflows/collect.yml`에는 아직 새 키·DB 연결 문자열·수집 명령이 연결되지 않았습니다. 예약 실행을 구현할 때 Repository Secret `NOWON_NOTICE_API_KEY`와 DB 연결 정보를 워크플로의 실행 단계 환경 변수로 전달해야 합니다. 실제 Secret 등록 여부는 확인하지 않았습니다.

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
