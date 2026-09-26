# Notice pipeline

Python·uv 기반 공지 수집 파이프라인입니다. 노원구 `NowonNewsNoticeList` API의 공지와 공개 원문 페이지의 파일 정보를 수집·변환해 PostgreSQL에 함께 저장합니다. 월계1동 수집과 Actions 예약 수집은 후속 작업입니다.

## 준비

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- 마이그레이션이 적용된 PostgreSQL DB (`collect-one`·`collect` 저장 시 필요)

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
| `DATABASE_URL` | 예 (저장 명령) | `psycopg`가 사용할 PostgreSQL 연결 문자열 |
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
- `NOWON_NOTICE_API_KEY`: `check-config --source nowon`, `collect-one --source nowon`, `collect --source nowon`에서 사용합니다. 설정 검사에는 DB 정보가 필요 없지만 **DB에 쓰는** 두 수집 명령에는 `DATABASE_URL`도 필요합니다. `SEOUL_API_KEY`는 이 명령들에 쓰지 않습니다. `sample`로 설정 형식 검사는 가능하지만 사용자 발급 키의 유효성은 실제 조회에서 별도 확인해야 합니다.
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

API `LINK`가 `http://www.nowon.kr:80/...`이어도 본문 파일의 상대 URL은 검증된 HTTPS 공지 주소를 기준으로 결합합니다. 본문과 원문 페이지의 파일 링크가 절대 HTTP 주소 또는 `//www.nowon.kr:80` 주소여도 **노원구 공식 호스트**의 파일 URL만 `https://www.nowon.kr/...`로 정규화합니다. 다른 호스트의 HTTP 주소는 임의로 HTTPS로 바꾸지 않습니다. `notices.body_html`은 원본 HTML 그대로 보존하므로, 앱이 그 HTML을 직접 렌더링할 때의 URL 처리·실기기 이미지 표시 검증은 별도 작업입니다. 이미 저장된 파일 URL이 이번 정규화로 달라지는 기존 공지는 재수집 시 `is_modified=true`가 될 수 있으므로 초기 적재 전에 이 버전을 적용하는 편이 안전합니다.

DB 마이그레이션의 파일 고유 제약은 `(notice_id, file_id, kind)`입니다. `file_sn`은 원본 메타데이터로 보존하며 같은 번호의 서로 다른 파일을 허용합니다. 한 입력에서 같은 `(file_id, kind)`가 서로 다른 URL로 반복되면 임의로 하나를 고르지 않고 수집 실패로 처리합니다. 전체 수집이 성공했을 때만 파일 목록을 DB에 전달합니다.

## 노원구 공지 여러 건 수집·저장

`collect --source nowon`은 API 목록 전체를 페이지별로 읽고 각 공지를 처리합니다. 원문 페이지와 첨부 영역을 정상적으로 읽은 공지만 공지와 파일 목록을 한 트랜잭션으로 저장합니다. 원문이 없거나 확인에 실패한 공지는 저장하지 않고 다음 공지로 진행합니다. `--limit 3`을 붙이면 앞의 3건만 처리해 전용 테스트 DB에서 소량 시험할 수 있습니다. 전체 목록이 3건보다 많으면 제한 실행은 `complete=false`, 종료 코드 1입니다. 명령은 **실제 DB에 쓰므로 운영 DB에서 시험하지 마세요.** 새 마이그레이션은 필요하지 않습니다.

```powershell
python -m uv run pipeline collect --source nowon --limit 3
python -m uv run pipeline collect --source nowon
```

결과 JSON의 `listing_complete`는 API 페이지 순회에서 발견 가능한 불일치가 없는지, `complete`는 처리 대상 공지가 모두 원문·첨부까지 확인되어 저장됐는지 나타냅니다. `saved_count`는 실제 저장된 공지 수입니다. `failures`에는 저장하지 못한 게시물 번호, 단계, 비밀값이 없는 `reason_code`가 들어갑니다. 건너뛴 공지가 한 건이라도 있으면 `complete=false`, 종료 코드 1입니다. 같은 명령을 다시 실행하면 `(category, post_sn)` 기준으로 중복 없이 갱신합니다.

원문 페이지의 일시적 타임아웃·429·일부 서버 오류는 최대 3회 시도합니다. HTTP 200이어도 페이지가 `데이터가 존재하지 않습니다.`를 반환하면 `page_missing`으로 구분합니다. 다른 첨부 오류는 `attachment_section_missing`, `file_reference_conflict` 등의 이유 코드로 구분합니다. 실패한 공지는 신규 저장하지 않으며, 기존 공지의 본문·메타데이터·파일·공개 상태도 변경하지 않습니다. 재수집 시 원문을 확인할 수 있으면 정상 저장하고 `is_visible=true`로 복원합니다. 확인 실패를 파일 0건으로 해석하지 않습니다. `is_pinned`는 공개 여부와 무관합니다.

기존 공지가 일시적으로 원문 확인에 실패해도 자동으로 숨기지 않으며, 원출처의 실제 삭제 여부를 판단하는 일괄 숨김 기능은 아직 없습니다. API 목록에서 같은 `post_sn`에 충돌이 있거나 변환·DB 저장 자체가 실패한 공지는 저장하지 않고 실행 JSON에만 보고합니다. 이 실패 목록의 영구 보관·알림·재처리는 후속 작업입니다. `complete=true`도 원본 게시판의 모든 글이 API에 있다는 절대적 보장은 아닙니다.

2026-09-26 현재 정책의 실공지 전체 시험에서는 API 목록 547건을 모두 처리해 원문·첨부 확인 공지 224건을 저장했고, 323건은 `page_missing/source_page_missing`으로 건너뛰었습니다. 전용 DB에서 공지 224행·파일 414행(첨부 251·본문 이미지 163), 비공개 공지·고아 파일 0건을 확인했습니다. 같은 명령을 두 번 실행한 뒤에도 공지·파일 행 수는 그대로이고 `is_modified=true`는 0건이었습니다. 과거 부분 저장 실험의 547행은 현재 정책에 적용되지 않습니다.

`transform/nowon.py`의 `transform_nowon_notice(notice)`는 API의 `RawNotice`를 DB 저장용 `NoticeRecord`로 바꿉니다. 노원구 출처·공공누리 유형을 확인하고, 등록일을 `date`로 변환하며, 원문 페이지 조회와 동일한 URL 규칙으로 HTTPS 주소를 만듭니다. 텍스트도 이미지·링크도 없는 본문만 `None`으로 바꾸고, 이미지나 링크만 있는 본문 HTML은 그대로 보존합니다. 변환 함수는 네트워크나 DB에 접근하지 않으며 `collect-one`에서 저장 직전에 호출합니다.

## 공지 단독 저장 함수

`storage/notices.py`의 `save_notice(conn, record)`는 변환된 `NoticeRecord`를 `notices`에 `(category, post_sn)` 기준으로 한 SQL 문에서 저장·갱신하고 DB `id`를 반환합니다. 새 공지는 공개 상태로 저장하고 다시 확인된 공지를 `is_visible=True`로 복원합니다. 기존 공지는 제목·본문 HTML·등록일·원문 URL·공공누리 유형 중 하나라도 이전 값과 다르면 `is_modified=True`가 되고, 이후 원래 값으로 돌아와도 `True`를 유지합니다. 부서만 변경되면 수정됨으로 표시하지 않습니다. `updated_at`은 갱신하며 `created_at`은 유지합니다. 동일값 비교에는 SQL의 `IS DISTINCT FROM`을 사용해 `NULL` 변경도 감지합니다.

함수는 `commit`, `rollback`, 연결 종료를 하지 않습니다. `DatabaseSettings.from_env()`는 DB 연결에 필요한 `DATABASE_URL`만 읽어 검증하므로 API 키 없이도 사용할 수 있습니다. `psycopg.connect(settings.database_url)`로 연결한 뒤 변환된 레코드를 함수에 전달합니다. `collect-one`은 아래의 공지·파일 묶음 저장 함수를 사용합니다.

## 공지와 파일 함께 저장

`storage/notice_bundle.py`의 `save_notice_with_files(conn, notice, files)`는 완전히 수집·변환된 공지와 파일 목록을 **공지 한 건 단위의 트랜잭션**으로 저장하고 `notices.id`를 반환합니다. 새 공지는 고유 키 `(category, post_sn)`의 `INSERT ... ON CONFLICT DO NOTHING RETURNING id` 결과로 구별하며 첫 파일 저장을 수정으로 표시하지 않습니다. 기존 공지는 7단계 upsert로 갱신합니다. 파일은 `(file_id, kind)`별로 `file_sn`, `file_name`, `url`을 비교하므로 입력 순서만 바뀌면 DB 파일 행과 `is_modified`를 그대로 둡니다. 파일 정보가 실제로 달라졌을 때만 기존 목록을 삭제·재삽입하고 기존 공지의 `is_modified=True`로 유지합니다. 파일 입력의 `(category, post_sn)`이 공지와 다르거나 같은 파일 키의 정보가 충돌하면 저장 전에 거부합니다.

`files=[]`는 **본문과 원문 페이지를 정상적으로 수집했는데 파일이 없는 경우**에만 전달해야 합니다. 페이지 요청·파싱이 실패하면 저장 함수를 호출하지 않습니다. 파일 저장 오류가 나면 공지 변경까지 롤백합니다. 함수가 독립 트랜잭션으로 실행되면 정상 종료 시 확정되며, 호출자가 이미 트랜잭션을 열었다면 내부 작업은 savepoint로 묶여 바깥 트랜잭션에 남습니다. `collect-one`도 완전 수집에 성공한 뒤에만 저장합니다.

실제 PostgreSQL 통합 테스트는 현재 **모든** 마이그레이션이 적용된 테스트용 DB에 `PIPELINE_TEST_DATABASE_URL`을 설정한 뒤 실행할 수 있습니다. 테스트는 고유한 게시물 번호를 사용합니다. 저장 계층 테스트는 종료 시 트랜잭션을 롤백하고 CLI·다건 통합 테스트는 확정된 해당 테스트 행만 삭제합니다. 이 변수를 설정하지 않으면 DB 통합 테스트가 건너뛰어지므로, 건너뛴 상태를 저장 검증 완료로 해석하면 안 됩니다. 원문 확인 공지만 저장하는 현재 정책은 임시 PostgreSQL 17.11에서 `370 passed, 0 skipped`로 검증했습니다. 실제 API 전체 수집 결과도 위와 같이 전용 DB에서 확인했습니다. 이전 부분 저장 실험 수치는 과거 정책 기록입니다.

XML은 인증키를 사용하는 공공 API의 응답 형식이며 RSS가 아닙니다. 실제 JSON 응답은 긴 `ID`를 부동소수점 지수형으로 내보내 끝자리가 달라진 사례가 있어, 문자 그대로 보존되는 XML의 `ID`를 사용합니다. `post_sn`은 문자열로 유지합니다. 월계1동 및 다른 출처도 API와 인증 방식을 확인한 뒤 추가할 계획입니다. 현재 RSS 수집 코드는 없습니다. 월계1동에 적합한 API가 없다면 그 출처의 수집 방식은 따로 결정해야 합니다.

`collect-one`은 한 번만 요청하고 자동 재시도하지 않습니다. `collect`은 API 목록 페이지와 원문 페이지의 일시적 오류를 제한적으로 재시도합니다. 리다이렉트는 따라가지 않습니다. 전체 성공은 종료 코드 0, 설정 오류는 2, 부분 실패를 포함한 수집·변환·DB 오류는 1입니다. 공식 API 주소는 `http://openapi.seoul.go.kr:8088`이며 HTTP 연결이라 전송 구간이 암호화되지 않습니다. 현재 환경에서 해당 서버의 HTTPS 연결 성공은 확인되지 않았습니다. 요청 로그의 키 마스킹은 네트워크 구간 암호화를 대신하지 못합니다.

변수의 정의·사용 위치는 `.env.example`, `src/pipeline/config.py`, `src/pipeline/sources/nowon_api.py`, `src/pipeline/cli.py`입니다. `.github/workflows/collect.yml`에는 아직 새 키·DB 연결 문자열·수집 명령이 연결되지 않았습니다. 예약 실행을 구현할 때 Repository Secret `NOWON_NOTICE_API_KEY`와 DB 연결 정보를 워크플로의 실행 단계 환경 변수로 전달해야 합니다. 실제 Secret 등록 여부는 확인하지 않았습니다.

## 실행과 검증

```powershell
python -m uv run python -m pipeline --help
python -m uv run pipeline check-config
python -m uv run pipeline check-config --source nowon
python -m uv run pipeline collect-one --source nowon
python -m uv run pipeline collect --source nowon --limit 3
python -m uv run ruff check
python -m uv run pytest
```

공식 설치 프로그램으로 `uv` 실행 파일이 `PATH`에 등록된 환경에서는 위 명령의
`python -m uv`를 `uv`로 줄여 실행할 수 있습니다.
