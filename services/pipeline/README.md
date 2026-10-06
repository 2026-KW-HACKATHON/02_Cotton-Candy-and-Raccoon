# Notice pipeline

Python·uv 기반 공지 수집 파이프라인입니다. 노원구 `NowonNewsNoticeList` API와 월계1동 공식 게시판의 공지·파일 정보를 수집·변환해 PostgreSQL에 함께 저장합니다. GitHub Actions 예약 실행은 별도 활성화 전까지 DB에 쓰지 않습니다.

## #18 현재 구현 범위와 새 저장 계약

노원구·월계1동·서울시 수집 모델·변환·저장은 #16의 통합 초기 스키마를 사용합니다. 비어 있는 DB에 아래 3개 SQL을 파일명 순서대로 적용해야 합니다. 별도의 identity 후속 SQL은 init에 통합되어 더 이상 적용하지 않습니다.

1. `20260922053900_init.sql`: 공지·파일의 최종 컬럼과 제약 생성
2. `20260922053901_rls.sql`: 앱의 공개 데이터 읽기 권한 설정
3. `20260923044500_holidays.sql`: 기존 공휴일 테이블·접근 제한 설정

파일별 역할과 제약은 [공지 DB README](../../supabase/README.md)를 참고하세요. 이 초기 구조는 이미 생성된 DB를 자동 변경하는 업그레이드 SQL이 아닙니다. CLI도 스키마를 생성하거나 마이그레이션을 자동 적용하지 않습니다.

공지 식별은 `(category, source_board, post_sn)`이고 source_board는 노원구 `1001`, 월계1동 게시판 `1042`입니다. 같은 게시판에 표시되는 다른 동 고정 공지도 source_board는 `1042`입니다. 서울시는 BLOG_ID를 사용합니다. SQL 구성은 5개에서 3개로 통합됐지만 BE의 최종 저장 계약은 같습니다.

파일 식별은 `(notice_id, file_key, kind)`입니다. `FileRecord.file_key`는 읽기 전용 계산 속성으로, 실제 ID가 있으면 `id:<file_id>`, 없으면 `url:<저장할 정규화 URL의 UTF-8 SHA256>`을 반환합니다. `file_sn`·`file_id`는 출처에 없을 때 None/SQL NULL이며, 빈 값이나 가짜 UUID를 넣지 않습니다. URL 해시는 파일 내용 해시가 아닙니다.

식별자가 없는 본문 이미지는 확인된 공식 `/webcontent/crosseditor/images/`의 PNG/JPEG/WebP 경로를 처리합니다. 장식 이미지·일반 링크는 제외합니다. 원문 첨부 목록의 PDF/HWP 등 직접 파일 경로는 식별자가 없어도 처리하지만, 불명확한 다운로드 endpoint나 식별자가 일부만 있는 URL은 여전히 오류입니다. 본문 HTML은 원칙적으로 API 원본을 보존하고 파일 메타데이터 URL만 HTTPS·절대 URL로 정리합니다.

노원구 API 본문의 `q_fileId`에 별표(`*`)가 하나라도 있으면 이미 수집하는 원문 페이지에서 복구합니다. 연속 별표는 개수와 관계없이 하나의 가려진 구간으로 처리하며, 한 ID 안의 여러 별표 구간도 지원합니다. 원문 게시물 번호·본문 영역을 확인한 뒤, 같은 태그 역할(`img/src` 또는 `a/href`), 같은 `file_sn`, UUID의 가려지지 않은 부분이 일치하고 UUID 형식 검증을 통과하는 **공식 다운로드 URL 하나**만 허용합니다. UUID를 추측하지 않습니다. 복구한 주소는 HTTPS 절대 URL로 본문 HTML에 반영하고, 그 본문에서 파일 메타데이터를 다시 추출하므로 `notices.body_html`과 `notice_files`가 같은 주소를 사용합니다. 복구한 HTML은 파서가 재직렬화하지만 별표가 없는 공지의 HTML은 변경하지 않습니다.

파일 추출·중복 검사는 원문 주소 복구 **이후**에 수행합니다. 서로 다른 파일의 UUID가 같은 별표 문자열로 제공돼도 먼저 원문 UUID로 구분하고, 실제 파일 식별자의 충돌 검사는 유지합니다. `javascript:`, `mailto:`, `data:` 등 HTTP(S)가 아닌 링크는 파일 식별 전에 무시하며 본문에서 삭제하지 않습니다. API와 원문의 URL 속성은 앞뒤 공백을 제거한 뒤 읽습니다. 공식 노원구 다운로드 경로는 복구 후보·본문 파일·원문 첨부 모두 같은 함수로 HTTPS와 쿼리를 정규화합니다. `q_fileSn`을 먼저 두고 나머지 쿼리 쌍을 정렬하며, 중복 파라미터·빈 값도 제거하지 않습니다. 동일한 URL의 쿼리 순서·인코딩 차이는 한 후보·파일로 취급하되, 쿼리 값이 다르거나 서로 다른 UUID가 일치하는 경우는 기존 충돌·모호성 오류를 유지합니다. 외부 호스트와 다른 경로의 쿼리는 재정렬하지 않습니다.

원문 확인 실패(`masked_file_source_invalid`), 후보 없음(`masked_file_unresolved`), 복수 후보(`masked_file_ambiguous`)이면 해당 공지를 저장하지 않고 기존 공지·파일·공개 상태를 유지합니다. 다건 수집은 다음 공지를 계속 처리합니다. 일반 링크의 별표는 파일 UUID 마스킹으로 간주하지 않습니다. 이 처리는 노원구에만 적용하며 서울시 API 전용 정책·월계1동 수집·DB 스키마·환경 변수는 변경하지 않습니다. 이미 별표 주소로 저장됐던 공지를 복구하면 본문·파일 변경 규칙에 따라 `is_modified=true`가 될 수 있습니다. 동일한 복구 결과를 반복 저장하면 추가 변경으로 판단하지 않습니다.

서울시는 `SeoulNewsList` API 한 건 읽기와 **API 본문 내 파일 메타데이터 추출·DB 형식 변환·공지/파일 원자적 저장**, 분야별 최초·정기 수집 CLI까지 구현했습니다. GitHub Actions 예약 실행은 아직 연결하지 않았습니다.

### 서울시 분야별 최초·정기 수집 — 지정 DB에 실제 쓰기

```powershell
# services/pipeline에서 실행. 발급받은 SEOUL_NEWS_API_KEY와 DATABASE_URL 필요
# 09시·13시용: 최신 API 목록에서 새 글만 변환·저장
.\.venv\Scripts\python.exe -m pipeline collect --source seoul --mode new
# 17시용: 최신 글의 본문·파일 변경도 확인
.\.venv\Scripts\python.exe -m pipeline collect --source seoul --mode refresh
# 한 분야만 시험할 때: 환경 분야(25)
.\.venv\Scripts\python.exe -m pipeline collect --source seoul --mode new --source-board 25
```

8개 분야를 각각 처리합니다. **해당 분야의 저장 이력이 없으면 두 모드 모두 최신 25건**, 이력이 있으면 기본 최신 10건이 대상입니다. 최초 8개 분야 합계는 최대 200건의 저장 시도이며, 성공 200건을 보장하는 것은 아닙니다. 평소 `new`는 최신 10건 중 아직 공개 상태로 저장되지 않은 글만 처리하고, `refresh`는 최신 10건을 모두 다시 확인합니다. 최신 10건이 모두 새 글이면 두 모드 모두 기존 공개 글을 만날 때까지 목록을 확장하여 추가 새 글도 처리합니다. 따라서 실제 처리 건수는 10건을 넘을 수 있습니다.

페이지 경계는 2건 겹쳐 읽고 중복·내용 충돌·총건수 변동·무진행을 검사합니다. 여러 페이지를 읽으면 첫 글과 총건수를 재확인합니다. API가 고정 스냅샷을 제공하지 않아 같은 총건수의 내부 재정렬 누락까지 보장할 수는 없습니다. API 수집·변환·파일 추출 실패는 저장하지 않으며 기존 본문·파일·공개 상태를 바꾸지 않습니다. 재확인에 성공한 숨김 글은 공개 상태로 복원하고, 목록에 없다고 일괄 숨기지는 않습니다.

API 요청 전 1초 간격을 두고 일시적 오류는 최대 3회 시도합니다(재시도 대기 2·4초). HTTP 429는 반복하지 않고 남은 분야 요청도 중단합니다. 다른 공지·분야의 실패는 분리하여 가능한 공지를 계속 저장합니다. 공지·파일 저장은 공지별 트랜잭션입니다.

JSON의 `boards`에는 분야별 `total_count`, `selected_count`, `saved_count`, `pages_read`, `initial_baseline`, `listing_complete`, `complete`, `failures`가 들어갑니다. `complete`는 DB 컬럼이 아니라 **이번 선택 범위의 성공 여부**입니다. 전체 과거 공지 적재 완료를 뜻하지 않습니다. 성공은 종료 코드 0, 부분 수집/저장 실패는 1, 설정·옵션 오류는 2입니다. 이 명령에는 `--mode`가 필수이고 `--limit`은 지원하지 않습니다. sample 키는 5건 제한 때문에 이 수집 모드에 사용할 수 없습니다.

최초 25건 중 일부만 저장되면 다음 실행은 저장 이력이 있는 분야로 판단합니다. 실패 글이 최신 10건 밖에 있으면 자동 재시도를 보장하지 못합니다. 실패 JSON 보관·영구 재처리 대기열은 후속 작업입니다. 최신 10건보다 오래된 글의 수정도 자동 확인 대상이 아닙니다. CLI는 현재 시간을 판단하거나 스스로 예약 실행하지 않습니다. **Actions·공식 DB 실행은 별도 승인 및 연결 전까지 하지 않습니다.**

이전 원문 크롤링 방식의 발급 키 검증(195건 저장·refresh 78건 성공)은 과거 이력입니다. 현재 서울시 API 전용 정책의 검증 결과와 한계를 혼동하지 마세요. API 전용 검증은 아래 정책과 작업 기록 step34를 기준으로 확인합니다.

API 전용 검증(2026-10-04): 통합3개 SQL을 적용한 임시 PostgreSQL에서 전체 pipeline **666 passed, 0 skipped**, DB 구조·권한 **39 passed, 0 skipped**, Ruff 통과. 실제 sample API8회로 8개 분야 총40건 변환 성공, 원문 요청·파일 다운로드0회. 교통21/517999의 API 본문652자·첨부1행을 두 번 저장해 본문 동일성·동일 notice_id·파일1행·is_modified=false를 확인했습니다. 개인 발급 키 검증 결과는 다음 문단과 같습니다.

추가 발급 키 검증 완료: 최초new200건 성공, 반복new0건 처리, refresh80건 성공. 모두 complete=true·종료0이었습니다. 실제 DB 공지200·본문200·파일611행(첨부296/이미지315) 유지, ID·본문·생성 시각·파일 행 유지, refresh의 updated_at만 분야별10건씩80건 갱신, 중복·고아·수정표시0을 확인했습니다. 외부 글의 실제 내용 변경은 발생하지 않았으며 변경 감지·롤백은 자동 테스트 결과와 구분합니다. 문화사이트 로고·SNS 아이콘 등 장식 이미지 후보24행의 필터 기준은 추가 검토가 필요합니다. 이번 임시 서버·DB·로그는 종료/삭제했으며 공식 DB에 쓰지 않았습니다.

### 서울시 공지 한 건 저장 — 지정 DB에 실제 쓰기

```powershell
# SEOUL_NEWS_API_KEY와 통합 SQL 3개가 적용된 DATABASE_URL이 필요합니다.
# 저장 대상이 테스트 DB인지 먼저 확인하세요. 공식 DB에 임의 실행하지 마세요.
.\.venv\Scripts\python.exe -m pipeline collect-one --source seoul --source-board 24 --index 2
```

`--source-board`는 분야별 BLOG_ID입니다(21 교통, 22 안전, 23 주택, 24 경제, 25 환경, 26 문화, 27 복지, 30 행정). 생략하면 전체 분야 목록을 조회합니다. `--index`는 선택한 목록 안에서 1부터 시작하는 **목록 순번**이고 POST_ID가 아닙니다. 기본 1, sample 키는 1~5만 허용합니다. 목록은 변동될 수 있어 같은 순번이 항상 같은 글을 뜻하지 않습니다. 실제 응답의 post_sn으로 중복 저장 여부를 판단합니다. 임의 POST_ID 조회 지원을 가정하지 않습니다.

API·본문 파일 추출·변환이 성공한 뒤에만 DB 연결을 열고 기존 `save_notice_with_files()` 트랜잭션을 사용합니다. 성공은 stored=true·notice_id와 종료 코드 0, 수집/저장 오류는 1, 설정/CLI 옵션 오류는 2입니다. 서울시 원문 페이지는 요청하지 않습니다. API 본문에 지원 파일 참조가 없으면 파일 0건이 정상이며, 잘못된 다운로드 참조는 추출 실패로 처리합니다. DB 오류 메시지에는 접속 URI나 원문 SQL 오류를 출력하지 않습니다. SQL 스키마·Actions·Gemini 코드는 이번 연결에서 바꾸지 않았습니다.

이전 원문 방식의 sample 저장·파일 HEAD 검증은 과거 이력이며 API 전용 검증을 대신하지 않습니다. 이 명령은 파일 다운로드·HEAD 요청을 수행하지 않습니다.

### 서울시 저장 전 결과 확인 — 읽기 전용

```powershell
.\.venv\Scripts\python.exe -m pipeline inspect-prepared --source seoul
.\.venv\Scripts\python.exe -m pipeline inspect-prepared --source seoul --source-board 24 --index 2
```

`SEOUL_NEWS_API_KEY`만 필요합니다. API 한 건 → POST_CONTENT 본문 내 파일 참조 추출 → NoticeRecord·FileRecord 변환 결과를 출력합니다. DB 연결·파일 다운로드·Gemini 호출은 하지 않으며 `stored=false`입니다. 성공은 종료 코드 0, 설정 오류 2, 수집·변환 오류 1입니다.

서울시는 **API 전용 수집**입니다. 원문 페이지를 크롤링하거나 파일을 다운로드하지 않습니다. `POST_CONTENT`를 본문으로 사용하고 `POST_EXCERPT`로 대체하지 않습니다. 내용 없는 HTML은 null로 정규화하며 이미지뿐인 HTML은 유지합니다. API 본문이 완전하다는 사용자 합의에 따른 가정이며 원문과의 일치를 검사한 결과가 아닙니다. 코드에서 본문 길이를 잘라 저장하지 않습니다. 앱 렌더링용 HTML 안전화는 별도 작업입니다.

공지 URL은 BLOG_ID별 공식 경로와 POST_ID로 구성합니다. 이 URL의 접근 가능 여부·리다이렉트·원문 삭제 여부는 확인하지 않습니다. 따라서 이전 `page_redirect`는 서울시 저장 조건이 아닙니다. API에 남아 있는 글은 원문이 열리지 않아도 API 데이터로 저장할 수 있으며, 이를 근거로 숨김 처리하지 않습니다.

API에 공지별 공공누리 필드가 없어 `license_type=None`으로 저장합니다. 데이터셋 이용 조건과 개별 공지 유형을 동일하다고 추정하지 않습니다. 등록일은 PUBLISH_DATE의 시각을 검증한 뒤 date로 변환하며 category=seoul, dong_group=None, is_pinned=False입니다.

본문의 img[src]와 PDF/HWP/HWPX 등 직접 파일 링크를 구분합니다. 상대·프로토콜 상대 주소를 절대 주소로 바꾸고 서울시 공식 파일 호스트만 HTTPS로 정규화합니다. 외부 HTTP 파일은 HTTPS 지원을 추정하지 않습니다. 동일 URL+kind는 중복 제거하며 같은 URL의 첨부·본문 이미지 역할은 둘 다 유지합니다. 서울시 UUID를 만들어 넣지 않고 file_sn/file_id=None, file_key=url:SHA256을 사용합니다. 파일명은 URL 경로의 마지막 부분을 디코딩해 사용합니다. 일반 신청 링크·썸네일·srcset 대체 이미지·본문 밖 장식과 본문 안에 섞인 공식 WordPress theme 아이콘은 파일 목록에 넣지 않습니다.

현재는 API 본문의 직접 파일 참조만 지원합니다. **API 본문에 없는 별도 첨부파일은 의도적으로 수집하지 않습니다.** 확장자 없는 다운로드 endpoint·JavaScript 링크의 전수 지원은 보장하지 않습니다. download 속성이 있는데 파일 유형을 판별하지 못하면 오류로 보고합니다. 파일 URL 해시는 다운로드 성공이나 내용 동일성을 증명하지 않습니다.

### 서울시 장식 이미지 필터

`attachments/seoul_html.py`의 `CULTURE_DECORATIVE_IMAGE_PATHS`는 실제 관측한 문화사이트 로고·SNS 경로4개입니다. 호스트가 `culture.seoul.go.kr`이고 경로가 `/_ui/images/main/cnl-common/` 아래의 `nLc-logo-culture.png`, `nLc-top-facebook.png`, `nLc-top-instargram.png`, `nLc-top-blog.png`와 정확히 일치하는 **img만** 파일 목록에서 제외합니다. 쿼리/fragment가 붙어도 적용합니다. 기존 공식 WordPress theme 이미지 제외는 유지합니다.

디렉터리 전체·logo라는 이름·alt·작은 크기만으로 이미지를 버리지 않습니다. 알 수 없는 배너·포스터·다른 호스트/경로의 파일·명시적 첨부 링크는 유지합니다. 본문 HTML 자체는 수정하지 않습니다. #13에서 HTML을 직접 파싱해 Gemini 입력 이미지를 다운로드한다면 같은 필터 연동이 별도로 필요합니다.

필터 추가 후 새 임시 PostgreSQL에서 전체 pytest **690 passed, 0 skipped**, Ruff 통과. 기존 장식 파일행이 있는 공지를 refresh하면 파일집합 변경으로 is_modified=true가 될 수 있으며, 실제DB 테스트에서 본문·ID 유지와 장식행 제거·반복저장을 확인했습니다. 위 발급 키 공지200·파일611행은 필터 추가 전 검증 결과입니다. 필터 적용 후 전체200건 재수집·장식후보24행 전부제거를 확인했다고 해석하지 마세요. SQL·환경 변수·Actions·공식 DB는 변경하지 않았습니다.

### API 한 건 확인 — DB에 저장하지 않음

새 환경 변수 `SEOUL_NEWS_API_KEY`는 서울시 `SeoulNewsList` 전용입니다. `config.py`의 `SeoulNewsSettings.from_env()`에서 읽고 `sources/seoul_api.py`가 사용합니다. 기존 `SEOUL_API_KEY`와 `NOWON_NOTICE_API_KEY`는 유지하며 서로 자동 대체하지 않습니다. API 확인에는 DATABASE_URL이 필요하지 않습니다.

services/pipeline 폴더에서 다음 명령을 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m pipeline check-config --source seoul
.\.venv\Scripts\python.exe -m pipeline inspect-one --source seoul
.\.venv\Scripts\python.exe -m pipeline inspect-one --source nowon
```

`inspect-one`은 각 API의 1/1 XML 응답을 읽고 식별 정보·제목·등록일·본문 길이와 `stored=false`를 출력합니다. DB 연결·원문 페이지 요청·파일 다운로드·Gemini 호출은 하지 않습니다. 성공은 종료 코드 0, 설정 오류 2, 수집 오류 1입니다. `check-config`는 형식만 검사하며 실제 인증을 확인하지 않습니다.

서울시 원본 `RawSeoulNotice`는 BLOG_ID→source_board, POST_ID→post_sn, POST_TITLE→title, PUBLISH_DATE→registered_on, MODIFY_DATE→modified_on, MANAGER_DEPT→department를 보존합니다. POST_CONTENT를 body_html로 쓰고 POST_EXCERPT는 별도로 보존하며 빈 본문을 미리보기로 대체하지 않습니다. 날짜는 이 단계에서 원본 문자열입니다. 원본 모델에는 API에 없는 URL·공공누리 값을 넣지 않고 이름·전화번호는 모델에 포함하지 않습니다.

발급 키를 로컬 파일에 쓰지 않고 PowerShell에서 입력할 수 있습니다. 키를 출력하거나 채팅에 보내지 마세요.

```powershell
$seoulSecret = Read-Host '서울시 새소식 API 키' -AsSecureString
$env:SEOUL_NEWS_API_KEY = [System.Net.NetworkCredential]::new('', $seoulSecret).Password
.\.venv\Scripts\python.exe -m pipeline inspect-one --source seoul

$nowonSecret = Read-Host '노원구 공지 API 키' -AsSecureString
$env:NOWON_NOTICE_API_KEY = [System.Net.NetworkCredential]::new('', $nowonSecret).Password
.\.venv\Scripts\python.exe -m pipeline inspect-one --source nowon
```

종료 후 필요하면 `Remove-Item Env:SEOUL_NEWS_API_KEY, Env:NOWON_NOTICE_API_KEY -ErrorAction SilentlyContinue`와 `Remove-Variable seoulSecret, nowonSecret -ErrorAction SilentlyContinue`로 현재 셸에서 해제합니다. 환경 변수는 현재 PowerShell과 자식 프로세스의 메모리에 전달되며 이 명령은 파일에 저장하지 않습니다. 이는 메모리에서의 완전한 비밀 삭제를 보장하는 기능은 아닙니다.

공식 API endpoint는 HTTP이며 키가 URL 경로로 전달됩니다. 로그는 키를 마스킹하지만 전송 구간은 암호화되지 않습니다. sample 검증 성공을 개인 키의 인증·승인 검증으로 해석하지 않습니다. 데이터 출처: [서울시 8개 분야의 새소식 정보](https://data.seoul.go.kr/dataList/OA-12605/A/1/datasetView.do).

## 환경 준비

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
- 월계1동 공식 게시판은 공개 HTML 페이지를 읽으므로 API 키가 필요 없습니다. `check-config --source wolgye1`은 타임아웃 형식만 확인하며, 저장 명령에는 `DATABASE_URL`이 필요합니다.
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

## 월계1동 공지 한 건 수집·저장

RSS나 별도 API 키를 사용하지 않고 [월계1동 공식 게시판](https://www.nowon.kr/dong/user/bbs/BD_selectBbsList.do?q_bbsCode=1042&q_deptCode=1047)의 목록과 원문 상세 HTML을 직접 읽습니다. 목록의 요약 문구를 본문으로 사용하지 않으므로 RSS의 본문 길이 제한을 받지 않습니다. 실행하면 **DB에 저장**하므로 전용 테스트 DB에서 먼저 확인하세요.

```powershell
$env:DATABASE_URL = "postgresql://사용자:비밀번호@127.0.0.1:54322/postgres"
python -m uv run pipeline check-config --source wolgye1
python -m uv run pipeline collect-one --source wolgye1
python -m uv run pipeline collect-one --source wolgye1 --post-sn 20260825100337962
python -m uv run pipeline collect-one --source wolgye1 --post-sn 게시물번호 --page 게시판페이지
```

기본값은 목록 **첫 페이지**입니다. `--post-sn`이 없으면 첫 번째 월계1동 일반 공지를 선택합니다. 실패한 오래된 글은 게시판에서 현재 페이지를 찾은 뒤 `--post-sn`과 `--page`를 함께 지정해 한 건씩 수동 재시도할 수 있습니다. 지정한 페이지에 해당 번호가 없으면 실패하며 임의로 상세 URL을 만들지 않습니다. 목록에 다른 동의 고정 공지가 함께 표시되면 `dong_group=other`, `is_pinned=true`로 구분합니다. 같은 게시물이 고정·일반 행에 중복되면 하나로 합칩니다.

`sources/wolgye1_board.py`는 상세 페이지의 게시물 번호·제목·부서·등록일·공공누리 유형과 전체 `.article-body` HTML을 읽습니다. `attachments/dong_html.py`는 본문 이미지·다운로드 링크와 별도 ‘첨부파일’ 목록의 URL, `q_fileSn`, `q_fileId`, 파일명을 추출하며 파일 본문은 다운로드하지 않습니다. `transform/dong.py`는 `category=dong`, 동 분류·고정 여부와 날짜·URL을 DB 형식으로 검증합니다. 상세 페이지가 없거나 게시물 번호 또는 첨부 영역을 확인할 수 없으면 저장하지 않습니다. 완전히 읽힌 한 건만 기존 `save_notice_with_files`로 공지·파일을 한 트랜잭션에 저장합니다.

`collect-one`은 월계1동 전체 수집이 아니라 첫 페이지 한 건 확인용입니다. 원문 HTML의 외부 이미지·링크가 모두 다운로드 가능한지도 보장하지 않습니다.

## 월계1동 공지 여러 건 수집·저장

`collect --source wolgye1`은 목록에 표시된 총 페이지 수까지 순회한 뒤, 각 공지의 상세 본문과 첨부 정보를 확인하여 **공지 한 건씩 독립된 트랜잭션**으로 저장합니다. 월계1동 게시판의 고정 공지에는 다른 동 글도 섞이므로 `dong_group=other`, `is_pinned=true`로 구분합니다. 같은 `post_sn`이 여러 페이지나 고정·일반 행에 나타나면 한 건으로 합칩니다. 완전 수집에는 많은 HTTP 요청이 필요하므로 먼저 전용 DB에서 `--limit`으로 시험하세요.

```powershell
python -m uv run pipeline collect --source wolgye1 --limit 26
python -m uv run pipeline collect --source wolgye1
```

`--limit`은 **고정 공지를 포함한 고유 게시물**의 처리 건수입니다. 제한에 걸리면 필요한 페이지까지만 읽고 `limited=true`, `complete=false`, 종료 코드 1을 반환합니다. `total_count`는 게시판에 표시된 일반 공지 총건수이고, `listed_count`에는 고정 공지가 추가될 수 있어 두 숫자가 같지 않아도 됩니다. `attempted_count`는 상세 처리 대상으로 선택한 수, `saved_count`는 실제 저장 성공 수입니다. `failed_pages`는 읽지 못한 목록 페이지 번호, `duplicate_count`는 페이지 사이 반복된 게시물 번호 수입니다.

## 월계1동 하루 3회 운영 모드

```powershell
# 한국 시간 09:00·13:00
python -m uv run pipeline collect --source wolgye1 --mode new

# 한국 시간 17:00
python -m uv run pipeline collect --source wolgye1 --mode refresh
```

`new`는 고정 공지를 제외한 최신 일반 공지 5건을 DB의 `(category, source_board, post_sn)`과 비교합니다. 기존 글은 상세 페이지를 요청하지 않고 새 글만 저장합니다. 5건이 모두 새 글이면 기존 저장 글을 만날 때까지 목록을 더 읽어 새 글을 저장합니다. 처음 실행해 저장된 월계1동 일반 공지가 하나도 없다면 전체 과거 목록을 긁지 않고 최신 5건만 초기 기준으로 저장합니다. `refresh`는 최신 일반 공지 5건과 목록 첫 페이지에 표시되는 **고정 공지 전체**의 상세 페이지를 다시 확인해 본문·파일 변경을 반영합니다. 고정 공지는 일반 공지 5건에 포함되지 않습니다. 연속 목록 페이지와 연속 상세 페이지 사이에는 1초를 기다리고, HTTP 429가 오면 곧바로 재시도하지 않고 남은 상세 요청도 멈춥니다. DB 스키마나 기존 공지의 공개 상태는 일괄 변경하지 않습니다.

이 모드의 `complete=true`는 **해당 실행의 선택 범위**가 성공했다는 뜻이지 게시판의 모든 과거 공지를 저장했다는 뜻이 아닙니다. 실패 건은 결과 JSON에 `post_sn`과 이유 코드로 남지만, DB에 실패 대기열을 추가하지 않았으므로 다음 실행 전에 최신 5건 밖으로 밀린 글은 자동 재시도할 수 없습니다. 실행 로그를 보고 게시판에서 페이지를 확인한 다음 `collect-one --post-sn ... --page ...`로 수동 재시도해야 합니다. Actions 연결 및 활성화 방법은 아래를 참고하세요.

각 목록 페이지의 표시 번호·총건수·행 수를 검사하고, 전체 순회 끝에 첫 페이지를 다시 읽어 목록 변동을 확인합니다. 일시적인 타임아웃·429·서버 오류는 최대 3회 시도합니다. 중간 페이지나 공지 한 건이 실패해도 다른 공지는 계속 처리하지만, 실패·누락·제한이 있으면 `complete=false`, 종료 코드 1입니다. 상세·첨부 확인이 실패한 공지는 새로 저장하거나 기존 파일을 빈 목록으로 교체하지 않습니다. **이번 명령은 기존 공지를 일괄 숨기지 않습니다.** 목록이 움직이며 생기는 모든 누락을 완전히 증명할 수 없으므로 결과의 `complete=true`도 원본 게시판의 절대적 전체성을 뜻하지 않습니다. 공개 상태 일괄 동기화·실패 목록의 영구 보관은 후속 작업입니다.

## 노원구 첨부파일 처리

`src/pipeline/attachments/nowon_html.py`의 `extract_files(notice)`는 API `DESCRIPTION`에서 본문 파일을, `extract_page_files(...)`는 원문 페이지의 ‘첨부파일’ 영역에서 별도 첨부를 읽습니다. `sources/nowon_page.py`가 원문 페이지 요청을 맡으며, 노원구 공지 URL·게시물 번호를 검증하고 HTTPS로 요청합니다. `q_fileSn`·`q_fileId`가 모두 있는 `<img src>`는 `inline_image`, 다운로드 `<a href>`는 `attachment`입니다. HTML 파서가 `&amp;`를 처리하고 상대 URL은 절대 URL로 바꿉니다. 같은 `(file_key, kind)`가 본문과 첨부 목록 양쪽에 있으면 원문 페이지의 첨부 정보를 우선하고, 같은 파일이 첨부와 본문 이미지 두 역할로 등장하면 각각 보존합니다. 파일 자체를 다운로드하거나 PDF·HWP 내용을 분석하지는 않습니다.

API `LINK`가 `http://www.nowon.kr:80/...`이어도 본문 파일의 상대 URL은 검증된 HTTPS 공지 주소를 기준으로 결합합니다. 본문과 원문 페이지의 파일 링크가 절대 HTTP 주소 또는 `//www.nowon.kr:80` 주소여도 **노원구 공식 호스트**의 파일 URL만 `https://www.nowon.kr/...`로 정규화합니다. 다른 호스트의 HTTP 주소는 임의로 HTTPS로 바꾸지 않습니다. `notices.body_html`은 원본 HTML 그대로 보존하므로, 앱이 그 HTML을 직접 렌더링할 때의 URL 처리·실기기 이미지 표시 검증은 별도 작업입니다. 이미 저장된 파일 URL이 이번 정규화로 달라지는 기존 공지는 재수집 시 `is_modified=true`가 될 수 있으므로 초기 적재 전에 이 버전을 적용하는 편이 안전합니다.

DB 마이그레이션의 파일 고유 제약은 `(notice_id, file_key, kind)`입니다. `file_sn`은 원본 메타데이터로 보존하며 같은 번호의 서로 다른 파일을 허용합니다. 한 입력에서 같은 `(file_key, kind)`가 서로 다른 URL로 반복되면 임의로 하나를 고르지 않고 수집 실패로 처리합니다. 전체 수집이 성공했을 때만 파일 목록을 DB에 전달합니다.

## 노원구 공지 여러 건 수집·저장

`collect --source nowon`은 API 목록 전체를 페이지별로 읽고 각 공지를 처리합니다. 원문 페이지와 첨부 영역을 정상적으로 읽은 공지만 공지와 파일 목록을 한 트랜잭션으로 저장합니다. 원문이 없거나 확인에 실패한 공지는 저장하지 않고 다음 공지로 진행합니다. `--limit 3`을 붙이면 앞의 3건만 처리해 전용 테스트 DB에서 소량 시험할 수 있습니다. 전체 목록이 3건보다 많으면 제한 실행은 `complete=false`, 종료 코드 1입니다. 명령은 **실제 DB에 쓰므로 운영 DB에서 시험하지 마세요.** 새 마이그레이션은 필요하지 않습니다.

```powershell
python -m uv run pipeline collect --source nowon --limit 3
python -m uv run pipeline collect --source nowon
```

### 노원구 하루 3회 운영 모드

```powershell
# 한국 시간 09:00·13:00: 새 글만
python -m uv run pipeline collect --source nowon --mode new

# 한국 시간 17:00: 최근 글 수정도 확인
python -m uv run pipeline collect --source nowon --mode refresh
```

DB에 노원구 공지가 하나도 없으면 **어느 모드든** API 최신 50건의 원문·파일 확인을 시도하고, 과거 전체를 자동 수집하지 않습니다. 이후 `new`는 API 최신 10건을 DB의 `(category, source_board, post_sn)`과 비교해 새 글만 원문 페이지로 들어갑니다. 10건이 모두 새 글이면 이미 저장된 글을 만날 때까지 API 목록을 확장합니다. `refresh`는 API 최신 10건의 원문·파일을 다시 확인해 실제 내용 변경을 반영합니다. 연속 API 페이지·상세 요청 사이에는 1초를 기다립니다. API 또는 원문 페이지에서 HTTP 429가 오면 즉시 재시도하지 않고 남은 상세 요청을 중단합니다.

`complete=true`는 **그 실행에서 선택한 범위**가 모두 성공했다는 뜻이지 API 전체를 수집했다는 뜻이 아닙니다. 최초 50건 중 원문 페이지가 열리지 않는 글은 현재 정책에 따라 저장하지 않고 `page_missing/source_page_missing`으로 보고하므로 `saved_count`가 50보다 작을 수 있습니다. 실패 번호는 실행 JSON에만 남으며, DB에 영구 재시도 목록을 추가하지 않았습니다. 실패 글이 이후 최신 10건 밖으로 밀리면 자동 복구를 보장하지 못합니다. 이 모드에는 발급받은 `NOWON_NOTICE_API_KEY`와 `DATABASE_URL`이 필요하고 `sample` 키는 사용할 수 없습니다.

결과 JSON의 `listing_complete`는 API 페이지 순회에서 발견 가능한 불일치가 없는지, `complete`는 처리 대상 공지가 모두 원문·첨부까지 확인되어 저장됐는지 나타냅니다. `saved_count`는 실제 저장된 공지 수입니다. `failures`에는 저장하지 못한 게시물 번호, 단계, 비밀값이 없는 `reason_code`가 들어갑니다. 건너뛴 공지가 한 건이라도 있으면 `complete=false`, 종료 코드 1입니다. 같은 명령을 다시 실행하면 `(category, source_board, post_sn)` 기준으로 중복 없이 갱신합니다.

원문 페이지의 일시적 타임아웃·일부 서버 오류는 최대 3회 시도합니다. 429는 즉시 반복 요청하지 않습니다. HTTP 200이어도 페이지가 `데이터가 존재하지 않습니다.`를 반환하면 `page_missing`으로 구분합니다. 다른 첨부 오류는 `attachment_section_missing`, `file_reference_conflict` 등의 이유 코드로 구분합니다. 실패한 공지는 신규 저장하지 않으며, 기존 공지의 본문·메타데이터·파일·공개 상태도 변경하지 않습니다. 재수집 시 원문을 확인할 수 있으면 정상 저장하고 `is_visible=true`로 복원합니다. 확인 실패를 파일 0건으로 해석하지 않습니다. `is_pinned`는 공개 여부와 무관합니다.

기존 공지가 일시적으로 원문 확인에 실패해도 자동으로 숨기지 않으며, 원출처의 실제 삭제 여부를 판단하는 일괄 숨김 기능은 아직 없습니다. API 목록에서 같은 `post_sn`에 충돌이 있거나 변환·DB 저장 자체가 실패한 공지는 저장하지 않고 실행 JSON에만 보고합니다. 이 실패 목록의 영구 보관·알림·재처리는 후속 작업입니다. `complete=true`도 원본 게시판의 모든 글이 API에 있다는 절대적 보장은 아닙니다.

2026-09-26 현재 정책의 실공지 전체 시험에서는 API 목록 547건을 모두 처리해 원문·첨부 확인 공지 224건을 저장했고, 323건은 `page_missing/source_page_missing`으로 건너뛰었습니다. 전용 DB에서 공지 224행·파일 414행(첨부 251·본문 이미지 163), 비공개 공지·고아 파일 0건을 확인했습니다. 같은 명령을 두 번 실행한 뒤에도 공지·파일 행 수는 그대로이고 `is_modified=true`는 0건이었습니다. 과거 부분 저장 실험의 547행은 현재 정책에 적용되지 않습니다.

`transform/nowon.py`의 `transform_nowon_notice(notice)`는 API의 `RawNotice`를 DB 저장용 `NoticeRecord`로 바꿉니다. 노원구 출처·공공누리 유형을 확인하고, 등록일을 `date`로 변환하며, 원문 페이지 조회와 동일한 URL 규칙으로 HTTPS 주소를 만듭니다. 텍스트도 이미지·링크도 없는 본문만 `None`으로 바꾸고, 이미지나 링크만 있는 본문 HTML은 그대로 보존합니다. 변환 함수는 네트워크나 DB에 접근하지 않으며 `collect-one`에서 저장 직전에 호출합니다.

## 공지 단독 저장 함수

`storage/notices.py`의 `save_notice(conn, record)`는 변환된 `NoticeRecord`를 `notices`에 `(category, source_board, post_sn)` 기준으로 한 SQL 문에서 저장·갱신하고 DB `id`를 반환합니다. 새 공지는 공개 상태로 저장하고 다시 확인된 공지를 `is_visible=True`로 복원합니다. 기존 공지는 제목·본문 HTML·등록일·원문 URL·공공누리 유형 중 하나라도 이전 값과 다르면 `is_modified=True`가 되고, 이후 원래 값으로 돌아와도 `True`를 유지합니다. 부서만 변경되면 수정됨으로 표시하지 않습니다. `updated_at`은 갱신하며 `created_at`은 유지합니다. 동일값 비교에는 SQL의 `IS DISTINCT FROM`을 사용해 `NULL` 변경도 감지합니다.

함수는 `commit`, `rollback`, 연결 종료를 하지 않습니다. `DatabaseSettings.from_env()`는 DB 연결에 필요한 `DATABASE_URL`만 읽어 검증하므로 API 키 없이도 사용할 수 있습니다. `psycopg.connect(settings.database_url)`로 연결한 뒤 변환된 레코드를 함수에 전달합니다. `collect-one`은 아래의 공지·파일 묶음 저장 함수를 사용합니다.

## 공지와 파일 함께 저장

`storage/notice_bundle.py`의 `save_notice_with_files(conn, notice, files)`는 완전히 수집·변환된 공지와 파일 목록을 **공지 한 건 단위의 트랜잭션**으로 저장하고 `notices.id`를 반환합니다. 새 공지는 고유 키 `(category, source_board, post_sn)`의 `INSERT ... ON CONFLICT DO NOTHING RETURNING id` 결과로 구별하며 첫 파일 저장을 수정으로 표시하지 않습니다. 기존 공지는 7단계 upsert로 갱신합니다. 파일은 `(file_key, kind)`별로 `file_sn`, `file_id`, `file_name`, `url`을 비교하므로 입력 순서만 바뀌면 DB 파일 행과 `is_modified`를 그대로 둡니다. 파일 정보가 실제로 달라졌을 때만 기존 목록을 삭제·재삽입하고 기존 공지의 `is_modified=True`로 유지합니다. 파일 입력의 `(category, source_board, post_sn)`이 공지와 다르거나 같은 파일 키의 정보가 충돌하면 저장 전에 거부합니다.

`files=[]`는 **본문과 원문 페이지를 정상적으로 수집했는데 파일이 없는 경우**에만 전달해야 합니다. 페이지 요청·파싱이 실패하면 저장 함수를 호출하지 않습니다. 파일 저장 오류가 나면 공지 변경까지 롤백합니다. 함수가 독립 트랜잭션으로 실행되면 정상 종료 시 확정되며, 호출자가 이미 트랜잭션을 열었다면 내부 작업은 savepoint로 묶여 바깥 트랜잭션에 남습니다. `collect-one`도 완전 수집에 성공한 뒤에만 저장합니다.

실제 PostgreSQL 통합 테스트는 현재 **통합 SQL 3개**가 적용된 테스트용 DB에 `PIPELINE_TEST_DATABASE_URL`을 설정한 뒤 실행할 수 있습니다. 테스트는 고유한 게시물 번호를 사용합니다. 저장 계층 테스트는 종료 시 트랜잭션을 롤백하고 CLI·다건 통합 테스트는 확정된 해당 테스트 행만 삭제합니다. 이 변수를 설정하지 않으면 DB 통합 테스트가 건너뛰어지므로, 건너뛴 상태를 저장 검증 완료로 해석하면 안 됩니다.

검증 이력을 구분합니다. SQL 통합 전 최종 구조(당시 5개 SQL)에서 전체 pipeline 테스트 `699 passed, 0 skipped`를 확인했습니다. 통합 후에는 별도 DB 테스트 `39 passed, 0 skipped`와 기존 최종 구조 대비 컬럼·제약·인덱스·RLS 동등성을 확인했습니다. 이번 문서 정리에서 전체 pipeline 테스트를 다시 실행한 것은 아닙니다. 과거 노원구 정책 검증의 `370 passed`와 부분 저장 실험 수치는 당시 이력으로 보존하며 현재 최신 검사 결과와 혼동하지 않습니다.

XML은 인증키를 사용하는 노원구 공공 API의 응답 형식이며 RSS가 아닙니다. 실제 JSON 응답은 긴 `ID`를 부동소수점 지수형으로 내보내 끝자리가 달라진 사례가 있어, 문자 그대로 보존되는 XML의 `ID`를 사용합니다. `post_sn`은 문자열로 유지합니다. 월계1동은 위와 같이 공식 HTML 게시판의 한 건 수집을 지원하며 RSS 수집 코드는 없습니다.

`collect-one`은 한 번만 요청하고 자동 재시도하지 않습니다. `collect`은 API 목록 페이지와 원문 페이지의 일시적 오류를 제한적으로 재시도합니다. 리다이렉트는 따라가지 않습니다. 전체 성공은 종료 코드 0, 설정 오류는 2, 부분 실패를 포함한 수집·변환·DB 오류는 1입니다. 공식 API 주소는 `http://openapi.seoul.go.kr:8088`이며 HTTP 연결이라 전송 구간이 암호화되지 않습니다. 현재 환경에서 해당 서버의 HTTPS 연결 성공은 확인되지 않았습니다. 요청 로그의 키 마스킹은 네트워크 구간 암호화를 대신하지 못합니다.

변수의 정의·사용 위치는 `.env.example`, `src/pipeline/config.py`, `src/pipeline/sources/nowon_api.py`, `src/pipeline/cli.py`입니다. GitHub Actions 연결은 아래와 같습니다. 실제 Secret 등록 여부는 확인하지 않았습니다.

## GitHub Actions에서 공식 DB에 저장

`.github/workflows/collect.yml`은 **GitHub Actions에서 실행될 때만** 수집 명령에 DB 접속 정보를 주입합니다. 이 파일을 편집하거나 로컬 테스트를 실행하는 것만으로 공식 DB에 접속하지 않습니다. 워크플로 파일이 기본 브랜치에 반영되어도 Repository Variable `PIPELINE_PRODUCTION_ENABLED`가 정확히 `true`가 아니면 수집 job 전체가 건너뛰어집니다. 준비와 팀 검토가 끝나기 전에는 이 변수를 만들지 않거나 `false`로 두세요.

GitHub 저장소의 Settings → Secrets and variables → Actions에서 아래를 설정합니다.

| 종류·이름 | 역할 |
| --- | --- |
| Secret `PIPELINE_DATABASE_URL` | **공식 Supabase DB의 직접 PostgreSQL 또는 pooler URI**. 워크플로에서만 `DATABASE_URL`로 전달합니다. Supabase의 HTTPS 프로젝트 URL이나 앱용 공개 키는 사용할 수 없습니다. |
| Secret `NOWON_NOTICE_API_KEY` | 노원구 `NowonNewsNoticeList` API의 발급 키. 워크플로에서 같은 이름의 환경 변수로 전달합니다. |
| Variable `PIPELINE_PRODUCTION_ENABLED` | `true`일 때만 수집 job 실행. 미설정·다른 값은 실행하지 않습니다. |

기존 `SUPABASE_URL`·`SUPABASE_SECRET_KEY`는 이 Python 코드의 `psycopg` 연결에 사용되지 않습니다. DB 접속용 계정은 현재 `notices`·`notice_files` 쓰기 권한이 필요하며, 연결 문자열과 비밀번호를 코드·PR·채팅·로그에 붙여 넣지 마세요. 공식 DB 스키마에 필요한 마이그레이션이 이미 적용되었는지도 활성화 전에 확인해야 합니다.

예약 시각은 한국 시간 **09:00·13:00 `new`, 17:00 `refresh`**입니다. GitHub 예약 워크플로는 기본 브랜치의 파일을 기준으로 실행됩니다. `workflow_dispatch`로 `new`/`refresh`를 수동 선택할 수도 있지만, 활성화 변수가 `true`이면 **수동 실행도 공식 DB에 실제 저장**합니다. 실행 전에 Secret 대상 DB를 다시 확인하세요. 두 출처는 각각 실행되며, 한 출처가 부분 실패해도 다른 출처를 시도합니다. 둘 중 하나라도 실패하거나 `complete=false`면 최종 Action은 실패로 표시되고, 각 출처의 결과 JSON에서 이유 코드를 확인할 수 있습니다. 이미 성공한 다른 공지의 DB 저장은 되돌리지 않습니다.

현재 워크플로와 Secret은 **코드 연결만 준비한 상태**입니다. 이 작업에서는 `PIPELINE_PRODUCTION_ENABLED`를 켜거나 공식 DB에 접속·저장하지 않았습니다.

## Gemini 요약 저장 계약

`summary_job.summarize_and_save_prepared_notice()`는 준비된 입력의 요약 결과와 실행 실패를
`notice_summaries`에 전달합니다. DB 연결·트랜잭션 확정과 마감일 계산 함수는 호출자가 제공하며,
테이블과 앱 읽기 권한은 #14에서 구성합니다.

- `summarized`: 필요한 근거가 모두 텍스트와 대조된 결과만 공개 `result`에 저장합니다.
- `needs_review`: 앱은 요약 대신 **“원문을 확인하세요”**를 안내합니다. 공개 `result`,
  `category`, `deadline_on`은 모두 NULL입니다. 파일 참조만 확인한 근거, 미확인 근거,
  불확실한 결과나 읽지 못한 첨부가 있으면 이 상태로 저장합니다. 텍스트와 파일 근거가
  섞인 일정도 검토 대상이며 마감일 계산 함수를 호출하지 않습니다.
- 실행 실패: 기존 행이 없을 때만 `status='failed'`, `result=NULL`로 삽입합니다. 반환된 실패
  상태는 이번 실행의 결과입니다. 기존 행이 있으면 저장된 `source_hash`와 이번 실행의 해시를
  비교합니다.
  - 해시가 같으면(모델·프롬프트 버전만 바뀐 재요약 등) `last_error_code`, `attempt_count`,
    `updated_at`만 갱신하고 기존 요약, 상태, 마감일, 메타데이터를 유지합니다.
  - 해시가 다르면(원문이 바뀐 뒤의 재요약) 옛 요약을 검증된 내용으로 공개하지 않습니다.
    `summarized` 행은 `needs_review`로 바뀌고 `result`, `category`, `deadline_on`은 NULL이
    됩니다. 다른 상태는 그대로 두되 공개 컬럼은 NULL로 비웁니다. `source_hash`와 `model`,
    `prompt_version`, `attachment_status`, `generated_at`은 마지막 성공 값을 유지해 새 원문으로는
    아직 요약에 성공하지 못했다는 사실을 남깁니다. 이후 새 해시로 요약에 성공하면 행 전체가
    새 결과로 바뀝니다.
- 재시도 대상 조회(#14): `status in ('pending', 'failed')`만으로는 원문 변경 후 실패해
  `needs_review`로 바뀐 행이 빠집니다. 재시도 기준(`last_error_code is not null` 또는 현재
  원문 해시와 저장 해시 비교)은 후속 이슈에서 정합니다. 이 저장 함수는 실패를 기록할 때만
  옛 요약을 숨기므로, 예상 밖 예외로 실패 기록 없이 끝난 실행이나 아직 요약하지 않은 원문
  변경은 재시도 조회에서 잡아야 합니다.
- 실행 전제: 공지 하나에 요약 작업은 한 번에 하나만 실행합니다. 같은 공지의 작업이 겹쳐
  늦게 끝난 실행이 먼저 쓰면, 이전 원문 기준의 실패가 새 요약을 `needs_review`로 바꾸거나
  이전 원문 기준의 성공이 새 요약을 덮어쓸 수 있습니다.

`StoredPreparedSummary.result`에는 파이프라인 호출자를 위한 원본의 메모리 스냅샷이 있습니다.
앱 응답으로 사용하지 않으며 `needs_review`의 공개 DB 행에는 저장하지 않습니다.
내부 검토 내용을 영구 보존하려면 #14에서 별도의 검토 테이블을 두고 `anon`·`authenticated`에는
SELECT 권한을 부여하지 않는 방안을 제안합니다. 이번 변경은 내부 검토용 테이블을 추가하지 않습니다.

`attempt_count`는 현재 누적 실행 횟수입니다. 성공 후 재시도 횟수 초기화와 PDF·이미지 내용을
포함하는 `source_hash` 계약은 후속 설계 대상입니다.

## 실행과 검증

```powershell
python -m uv run python -m pipeline --help
python -m uv run pipeline check-config
python -m uv run pipeline check-config --source nowon
python -m uv run pipeline collect-one --source nowon
python -m uv run pipeline check-config --source wolgye1
python -m uv run pipeline collect-one --source wolgye1
python -m uv run pipeline collect --source wolgye1 --limit 26
python -m uv run pipeline collect --source wolgye1
python -m uv run pipeline collect --source nowon --limit 3
python -m uv run ruff check
python -m uv run pytest
```

공식 설치 프로그램으로 `uv` 실행 파일이 `PATH`에 등록된 환경에서는 위 명령의
`python -m uv`를 `uv`로 줄여 실행할 수 있습니다.
