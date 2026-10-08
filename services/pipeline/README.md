# Notice pipeline

Python·uv 기반 공지 수집 파이프라인입니다. 노원구 `NowonNewsNoticeList` API와 월계1동 공식 게시판의 공지·파일 정보를 수집·변환해 PostgreSQL에 함께 저장합니다. GitHub Actions 예약 실행은 별도 활성화 전까지 DB에 쓰지 않습니다.

## #18 현재 구현 범위와 새 저장 계약

노원구·월계1동·서울시 수집 모델·변환·저장은 `supabase/migrations`의 스키마를 사용합니다. 비어 있는 DB에는 `supabase/migrations`의 SQL 전체를 파일명 순서대로 적용하고, 기존 DB에는 아직 적용하지 않은 새 마이그레이션을 추가합니다.

1. `20260922053900_notices.sql`: 공지·파일의 컬럼과 제약, 앱 읽기 권한
2. `20260922053901_holidays.sql`: 공휴일 테이블과 앱 접근 차단
3. `20260922053902_notice_summaries.sql`: 요약, 요약 실행, 원문 변경 trigger
4. `20260922053903_notice_easy_texts.sql`: 쉬운말 결과
5. `20261008150000_app_notice_views.sql`: develop의 앱 목록·상세 공개 조회 계약
6. `20261008160000_standard_dictionary_cache.sql`: 표준국어대사전 공유 캐시와 조회 권한
7. `20261008170000_notice_dictionary_candidates.sql`: 쉬운말 사전 후보
8. `20261008190000_notice_dictionary_links.sql`: 공지별 사전 뜻풀이 연결과 조회
9. `20261008210000_notice_processing_jobs.sql`: 기능별 재처리 상태와 점유 기한
10. `20261008220000_app_notice_views.sql`: backend 앱 조회 계약 호환 재적용

재구성 전 마이그레이션 14개를 적용한 로컬 DB는 다시 만들어야 합니다(`npx supabase db reset`).

파일별 역할과 제약은 [공지 DB README](../../supabase/README.md)를 참고하세요. 이 초기 구조는 이미 생성된 DB를 자동 변경하는 업그레이드 SQL이 아닙니다. CLI도 스키마를 생성하거나 마이그레이션을 자동 적용하지 않습니다.

공지 식별은 `(category, source_board, post_sn)`이고 source_board는 노원구 `1001`, 월계1동 게시판 `1042`입니다. 같은 게시판에 표시되는 다른 동 고정 공지도 source_board는 `1042`입니다. 서울시는 BLOG_ID를 사용합니다. SQL 파일 구성이 바뀌어도 BE의 저장 계약은 같습니다.

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

디렉터리 전체·logo라는 이름·alt·작은 크기만으로 이미지를 버리지 않습니다. 알 수 없는 배너·포스터·다른 호스트/경로의 파일·명시적 첨부 링크는 유지합니다. 본문 HTML 자체는 수정하지 않습니다. #13 입력 준비도 같은 장식 필터를 재사용합니다. 수집된 파일이라도 다운로드 허용 주소·지원 형식을 벗어나면 입력 준비는 실패할 수 있습니다.

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
| `STDICT_API_KEY` | 사전 캐시 미스·명시적 갱신 때 필수 | 서버 전용 표준국어대사전 API 인증키 |
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

저장할 때마다 `body_text`에 `notice_body_text(body_html)`(= `html_to_notice_text`, 요약 입력과 같은 평문)를 함께 씁니다. 평문이 비면 NULL입니다. 앱은 HTML 대신 이 평문을 표시합니다. 파생 값이라 수정됨 판단과 원문 변경 trigger의 비교 대상이 아닙니다.

`body_text` 컬럼이 생기기 전에 저장한 행은 다시 수집하면 채워집니다(내용이 같아도 upsert가 `body_text`를 씁니다). 목록에서 사라져 다시 수집되지 않는 행은 아래처럼 채웁니다. `body_text`만 바꾸므로 `content_revision`과 기존 요약은 그대로입니다.

```python
import psycopg
from pipeline.config import DatabaseSettings
from pipeline.storage.notices import notice_body_text

with psycopg.connect(DatabaseSettings.from_env().database_url) as conn:
    rows = conn.execute(
        "select id, body_html from notices where body_text is null and body_html is not null"
    ).fetchall()
    for notice_id, body_html in rows:
        conn.execute(
            "update notices set body_text = %s where id = %s "
            "and body_html is not distinct from %s and body_text is null",
            (notice_body_text(body_html), notice_id, body_html),
        )
```

조회 후 본문 HTML이 바뀌었거나 다른 작업이 평문을 채운 행은 갱신하지 않습니다. 건너뛴 행은 다음 실행에서 최신 원문을 다시 읽어 처리합니다.

함수는 `commit`, `rollback`, 연결 종료를 하지 않습니다. `DatabaseSettings.from_env()`는 DB 연결에 필요한 `DATABASE_URL`만 읽어 검증하므로 API 키 없이도 사용할 수 있습니다. `psycopg.connect(settings.database_url)`로 연결한 뒤 변환된 레코드를 함수에 전달합니다. `collect-one`은 아래의 공지·파일 묶음 저장 함수를 사용합니다.

## 공지와 파일 함께 저장

`storage/notice_bundle.py`의 `save_notice_with_files(conn, notice, files)`는 완전히 수집·변환된 공지와 파일 목록을 **공지 한 건 단위의 트랜잭션**으로 저장하고 `notices.id`를 반환합니다. 새 공지는 고유 키 `(category, source_board, post_sn)`의 `INSERT ... ON CONFLICT DO NOTHING RETURNING id` 결과로 구별하며 첫 파일 저장을 수정으로 표시하지 않습니다. 기존 공지는 7단계 upsert로 갱신합니다. 파일은 `(file_key, kind)`별로 `file_sn`, `file_id`, `file_name`, `url`을 비교하므로 입력 순서만 바뀌면 DB 파일 행과 `is_modified`를 그대로 둡니다. 파일 정보가 실제로 달라졌을 때만 기존 목록을 삭제·재삽입하고 기존 공지의 `is_modified=True`로 유지합니다. 파일 입력의 `(category, source_board, post_sn)`이 공지와 다르거나 같은 파일 키의 정보가 충돌하면 저장 전에 거부합니다.

`files=[]`는 **본문과 원문 페이지를 정상적으로 수집했는데 파일이 없는 경우**에만 전달해야 합니다. 페이지 요청·파싱이 실패하면 저장 함수를 호출하지 않습니다. 파일 저장 오류가 나면 공지 변경까지 롤백합니다. 함수가 독립 트랜잭션으로 실행되면 정상 종료 시 확정되며, 호출자가 이미 트랜잭션을 열었다면 내부 작업은 savepoint로 묶여 바깥 트랜잭션에 남습니다. `collect-one`도 완전 수집에 성공한 뒤에만 저장합니다.

실제 PostgreSQL 통합 테스트는 `supabase/migrations`의 **SQL 전체**가 적용된 테스트용 DB에 `PIPELINE_TEST_DATABASE_URL`을 설정한 뒤 실행할 수 있습니다. 테스트는 고유한 게시물 번호를 사용합니다. 저장 계층 테스트는 종료 시 트랜잭션을 롤백하고 CLI·다건 통합 테스트는 확정된 해당 테스트 행만 삭제합니다. 이 변수를 설정하지 않으면 DB 통합 테스트가 건너뛰어지므로, 건너뛴 상태를 저장 검증 완료로 해석하면 안 됩니다.

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

예약 시각은 한국 시간 **09:00·13:00 `new`, 17:00 `refresh`**입니다. GitHub 예약 워크플로는 기본 브랜치의 파일을 기준으로 실행됩니다. `workflow_dispatch`로 `new`/`refresh`를 수동 선택할 수도 있지만, 활성화 변수가 `true`이면 **수동 실행도 공식 DB에 실제 저장**합니다. 실행 전에 Secret 대상 DB를 다시 확인하세요. 노원·월계1동 및 선택적으로 활성화한 서울시 출처는 각각 실행되며, 한 출처가 부분 실패해도 다른 출처를 시도합니다. 활성화한 출처 중 하나라도 실패하거나 `complete=false`면 최종 Action은 실패로 표시되고, 각 출처의 결과 JSON에서 이유 코드를 확인할 수 있습니다. 이미 성공한 다른 공지의 DB 저장은 되돌리지 않습니다.

현재 워크플로와 Secret은 **코드 연결만 준비한 상태**입니다. 이 작업에서는 `PIPELINE_PRODUCTION_ENABLED`를 켜거나 공식 DB에 접속·저장하지 않았습니다.

## HWP 첨부 본문 텍스트 추출

`attachments/hwp_text.py`의 `extract_hwp_text(file)`은 다운로드 계층의
`DownloadedAttachment`를 받아 `HwpTextResult`를 반환합니다. `result.attachment`는
기존 요약기의 `AttachmentText(name, text)` 계약에 맞으며 `NoticeInput.attachments`에
전달할 수 있습니다. 이 함수 자체는 다운로드·DB 저장·Gemini 호출을 하지 않습니다.

```python
from pipeline.attachments.download import download_attachment
from pipeline.attachments.hwp_text import extract_hwp_text

downloaded = download_attachment(file_url, file_name)
result = extract_hwp_text(downloaded)
attachment_text = result.attachment
extraction_warnings = result.warnings
```

일반 HWP 5.0/5.1의 압축·비압축 본문과 모든 구역의 문단·표 셀 텍스트를 읽습니다.
미리보기 `PrvText`로 대체하거나 임의로 본문을 자르지 않습니다. 입력 파일은 최대
50 MiB, DocInfo와 모든 본문 구역을 합친 압축 해제 결과는 기본 20 MiB로 제한합니다.
제한을 넘거나 파일이 손상되면 `HwpExtractionError.reason_code`로 실패를 구분합니다.
HWPX·구형 HWP·암호/배포용/변경 추적 문서는 현재 지원하지 않습니다.

표의 행·열 배치는 보존하지 않으며 `table_layout_not_preserved` 경고를 반환합니다.
문서 안 그림·수식 등의 비텍스트 객체는 추출하지 않고 발견 시
`nontext_content_not_extracted`로 표시합니다. 특수 사설 영역 문자는 원문 그대로
남기고 `private_use_characters`로 표시합니다. 따라서 텍스트 추출 성공을 문서 전체의
시각 정보 확보로 해석하면 안 됩니다. 경고의 최종 요약 정책과 자동 처리 연동은 후속 작업입니다.

실제 노원구 HWP 샘플에서 2,102자를 추출했습니다. 샘플은 표를 포함하므로 배치 손실
경고가 있었습니다. 자동 검증은 원본 파일을 저장소에 넣지 않고 합성 OLE/HWP 파일로
정상·손상·압축·구역 누락·용량 제한과 기존 입력 계약 연결을 확인합니다.

본 제품은 한글과컴퓨터의 한글 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.

## DB 원본에서 요약 입력 준비

`storage/summary_source.py`의 `load_summary_source(conn, notice_id)`는 공개 공지 한 건과
연결된 파일 목록을 한 SELECT로 읽습니다. 없는 공지·숨긴 공지는 `None`을 반환합니다.
DB 내부 notice_id로 연결하므로 게시판별 post_sn 충돌 없이 조회합니다. 반환 모델에는
category/source_board/post_sn, file_key와 nullable file_id/file_sn, 조회 당시의
`content_revision`을 보존합니다. 이 버전을 저장 job에 그대로 전달해야 합니다.
파일 순서는 file_key/kind/id이며, file_key는 DB 값을 사용하고 가짜 식별자를 만들지 않습니다.
조회 함수는 저장·commit·연결 종료를 하지 않습니다. 읽기 트랜잭션은 다운로드 전에
호출자가 종료하고, Gemini 준비에는 연결 없이 반환된 데이터를 사용하세요.

```python
from datetime import datetime, timezone

import psycopg

from pipeline.attachments.summary_bundle import prepare_summary_source
from pipeline.storage.summary_source import load_summary_source

# database_url과 notice_id는 호출자가 지정합니다. 공식 DB 쓰기는 없습니다.
with psycopg.connect(database_url) as conn:
    source = load_summary_source(conn, notice_id)
if source is None:
    raise ValueError("공개 공지를 찾을 수 없습니다.")
prepared = prepare_summary_source(source, reference_datetime=datetime.now(timezone.utc))
if prepared.failures:
    # stage/item_id/reason_code를 기록하고 재처리. 원문·URL·비밀값은 로그에 넣지 않습니다.
    raise ValueError("요약 가능한 입력 준비 실패")
# 경고가 있으면 표 배치/내부 그림 등의 정보 손실 정책을 먼저 확인합니다.
warnings = prepared.warnings
input_blocks = prepared.to_gemini_input()  # 입력 생성만 함. 실제 API 호출 아님.
```

`attachments/summary_bundle.py`는 본문 HTML을 텍스트로 바꾸고 HTML의 이미지를
다운로드하며, DB 파일 목록의 PDF는 원본 바이트, HWP는 `AttachmentText`, PNG/JPEG/WebP는
이미지 바이트로 준비합니다. 식별자가 없는 서울시 URL 기반 파일도 처리하며, HTML에만
있는 월계1동 이미지는 기존 허용 주소 규칙 안에서 처리합니다. 노원구 주소에 더해
HTTPS news.seoul.go.kr·culture.seoul.go.kr의 직접 PDF/HWP/PNG/JPEG/WebP를 허용합니다.
파일명이 없으면 URL의 명확한 파일명을 사용하고 확장자 없는 endpoint는 추정하지 않습니다.
news.seoul.go.kr의 HTTP 주소는 수집기와 동일하게 HTTPS로 정규화합니다.
외부 호스트·그 밖의 서울시 HTTP 주소·리다이렉트·지원하지 않는 형식은 누락으로 보고합니다.
이는 파일 다운로드이며 서울시 원문 페이지 크롤링이 아닙니다.

본문과 DB 목록의 동일한 정규화 이미지 URL은 준비 실행 내 캐시로 한 번만 요청합니다.
실패한 URL도 같은 실행에서 재요청하지 않고 각 참조에 실패 코드를 남깁니다.
다른 URL에서 내려받은 같은 이미지는 캐시에서도 하나의 바이트 객체를 공유합니다. 서울시 장식
필터를 재사용하며 원본 HTML·DB 파일 목록은 수정하지 않습니다.

`prepared.file_manifest`에는 원문 버전과 원본 파일 행별 처리 결과를 함께 보존합니다.
같은 URL이나 바이트를 입력 하나로 합쳐도 원본 파일 ID·종류·URL은 모두 남기며,
PDF·이미지는 실제 `media_N` 블록, HWP는 추출 텍스트 위치와 해시로 연결합니다.
DB 목록에 없는 본문 이미지는 가짜 파일 ID 대신 원문 공지 링크로 안내합니다.
과거 DB에 남아 있는 장식 이미지는 다운로드하지 않고 `unread` 및
`decorative_image_ignored` 경고를 남겨 읽기 성공으로 집계하지 않습니다.

준비 결과를 실제 요약·저장으로 연결할 때는 다음 계약을 사용합니다. 아래 코드는
Gemini를 호출하고 지정한 DB에 저장하므로 테스트 환경에서 먼저 확인하세요.
입력 전체를 사용할 수 없는 준비 실패도 job에 전달하면 API 호출 없이 실패를 기록하고
같은 원문 버전의 기존 요약은 보존합니다. 일부 파일만 읽지 못하면 아래 부분 요약 계약을 따릅니다.

```python
from pipeline.storage.summary_metadata import build_summary_metadata_from_manifest
from pipeline.summary_job import summarize_and_save_prepared_notice
from pipeline.transform.gemini_client import DEFAULT_MODEL

metadata = build_summary_metadata_from_manifest(
    notice=prepared.notice, file_manifest=prepared.file_manifest, model=DEFAULT_MODEL,
)
with psycopg.connect(database_url, autocommit=True) as conn:
    outcome = summarize_and_save_prepared_notice(
        conn, prepared, metadata, expected_source_revision=source.content_revision,
    )
```

준비 후 원문이나 파일 목록이 변경되면 `superseded`로 종료하며 Gemini를 호출하지 않습니다.
저장 직전 변경도 버전 검사로 차단합니다. 자동 실행·예약·재시도는 별도 실행 계층의 책임입니다.

지원하지 않는 파일·이름을 판단할 수 없는 일반 첨부·다운로드/추출 실패는 `warnings`와
`file_manifest.omissions`에 남깁니다. 읽은 본문·첨부가 있으면 해당 범위만 요약하고
`needs_review`, `deadline_on=NULL`로 저장합니다. 첨부를 하나도 못 읽었으면 `unread`,
일부 읽었으면 `partial`입니다. DB 행이 없는 본문 이미지 누락도 별도로 검토 상태를 강제합니다.
Gemini에는 누락 내용을 추측하지 말라는 처리 범위를 전달하고, 결과에는 미확인 안내를 추가합니다.
제목만 있고 읽은 자료가 없거나 최종 입력 크기가 초과되면 `failures`로 차단합니다.
원문 버전·파일 연결 정보 검증 오류도 부분 요약으로 우회하지 않습니다.
`complete=True`는 전송 가능한 입력이 있다는 뜻이며, 전체 자료 읽기 성공을 뜻하지 않습니다.

같은 원문 버전에 기존 결과가 있으면 새 부분 요약으로 교체하지 않는 보수적 정책을 사용합니다.
최초 부분 요약은 공개할 수 있고, 이후 전체 첨부 준비가 성공하면 갱신할 수 있습니다.
기존 부분 요약보다 더 많은 자료를 읽은 경우에도 일부 누락이 남아 있으면 기존 결과를 유지합니다.
재시도 가능 여부와 예약은 별도 실행 계층에서 `reason_code`를 사용해 판단합니다.

이미지 한 장은 10 MiB, 본문 이미지 합계는 `max_body_image_bytes`(기본 50 MiB)로 분리합니다.
파일별 `max_seconds`는 기본 60초, 공지별 `max_preparation_seconds`는 기본 180초입니다.
기본 다운로드는 내부 비동기 I/O와 전체 timeout으로 응답 헤더·본문 대기를 취소하고 연결을 닫습니다.
시간 초과 파일은 `time_limit` 누락으로 남고, 공지 예산을 소진하면 다음 파일을 요청하지 않습니다.
호출 API는 동기 방식이며, 이미 실행 중인 이벤트 루프 안에서는 별도 스레드에서 요청을 완료합니다.
외부 `httpx.Client`를 직접 주입한 호환 경로는 호출자의 연결을 강제로 닫지 않으며 요청·청크 경계에서
단조 시계로 시간을 검사하고 요청 timeout을 남은 시간 이하로 제한합니다. 이 경로의 실행 중 I/O
취소는 클라이언트 소유자의 책임입니다. 운영 시 전체 대기 취소가 필요하면 기본 경로를 사용하세요.

`max_input_bytes`는 기본 50 MiB의 로컬 안전 한도로, 다운로드 보관량과 최종 JSON 입력의
텍스트·Base64 크기를 제한합니다. Gemini 모델별 실제 한도와 호출 비용을 보장하는 값은
아닙니다. 반환 블록은 기존 `media_input.py` 계약을 사용합니다. 최신 develop에는
PDF·이미지 입력을 받는 요약 경로가 추가됐습니다. #13 준비 계층에서 조원의
입력 검증 함수까지 실제 자료로 확인했으며, Gemini 전송·응답은 별도입니다. 추출 텍스트·파일을 DB에 다시
저장하거나 새 테이블·환경 변수·예약 실행을 추가하지 않았습니다.

현재 호환 회귀는 `tests/unit/attachments/test_summary_bundle_manifest.py`에서 중복 파일 연결, HWP 텍스트 해시,
본문 이미지 원문 링크, 준비 실패와 원문 버전 변경을 검증합니다. 전용 로컬 PostgreSQL에서
실제 원본 조회→입력 준비→요약 job→결과·파일 연결 저장도 검증하며,
이 테스트의 다운로드와 Gemini 응답은 대역을 사용합니다.
추가 회귀는 `tests/unit/attachments/test_attachment_recovery.py`에서 URL 오류 격리, 캐시 공유, 이미지 합계 예산,
시간 예산, 부분 요약 저장·재조회·복구·기존 결과 보존을 검증합니다.
2026-10-08 검증: develop `edc975b` 통합 상태에서 pipeline **3559 passed**, DB 스키마
**506 passed**, 모두 **0 skipped**. 공유 URL 정규화 변경 후 관련 회귀도 통과했습니다.
기본 다운로드의 헤더·본문 대기 취소는 비동기 HTTP 대역으로, 저장·권한은 로컬 PostgreSQL로
확인했으며 실제 Gemini 유료 호출이나 운영 DB 변경은 수행하지 않았습니다.

실제 추가 검증(2026-10-06): 전용 DB 저장·조회·롤백 후 월계1동 PDF, 복구된 노원구 PNG,
서울시 JPEG를 다운로드해 `prepare_gemini_request()`까지 통과했습니다. 별도로 월계1동
공지 `20260102161718470`의 HWP 2개에서 1,466자·1,212자를 추출했고 이미지 5개·PDF 1개도
입력 준비에 포함했습니다. HWP 표 배치 손실 경고는 유지했고 모든 DB 변경은 롤백했습니다.
이는 실제 Gemini 호출·요약 정확도·결과 저장 검증이 아닙니다.

## Gemini 요약 저장 계약

`summary_job.summarize_and_save_prepared_notice()`는 준비된 입력을 Gemini로 요약하고
`notice_summaries`에 결과 또는 실패를 저장합니다. 입력 준비·DB 연결·commit은 호출자가 담당합니다.
SQL 적용 순서와 앱 조회 권한은 [DB README](../../supabase/README.md)를 참고하세요.

연동 시 다음 값을 준비합니다.

| 값 | 기준 |
| --- | --- |
| `metadata` | `storage.summary_metadata.build_summary_metadata()`로 생성 |
| `source_hash` | 본문 평문과 `SummaryAttachmentText(file_key, text)`를 파일 키 순으로 정렬한 JSON의 SHA-256 |
| 첨부 상태 | 원래 파일 수와 읽은 파일 수로 계산. 추출된 텍스트 목록만으로 판단하지 않음 |
| `model` | 실제 호출 모델을 명시. Gemini 기본 모델은 `transform.gemini_client.DEFAULT_MODEL` (`gemini-3.5-flash-lite`) |
| `prompt_version` | `transform.gemini_prompt.SUMMARY_PROMPT_VERSION` (`notice-summary-v6-card-grounding`) |
| `expected_source_revision` | 필수 정수. 원문·파일 목록과 같은 스냅샷에서 조회한 `notices.content_revision` |

마감일은 기본 함수 `storage.summary_deadline.compute_deadline_on()`이 `application`,
`submission`, `payment`의 종료일 중 가장 늦은 날짜로 계산합니다. 해당 날짜가 없거나
`needs_review`이면 `deadline_on=NULL`입니다.

### 결과와 실패 처리

- `summarized`: 필요한 근거가 텍스트와 대조된 요약을 저장합니다.
- `needs_review`: 파일 참조만 확인된 근거, 미확인 내용, 불확실성 또는 읽지 못한 첨부가
  있으면 AI 요약과 네 카드를 보존하며, 검토 안내는 별도 `message`로 반환합니다.
  정렬용 `deadline_on`은 비웁니다.
- 실행 실패: job의 원문 버전과 실행 토큰이 유효하면 `last_error_code`, `attempt_count`,
  `updated_at`만 갱신하고 기존 요약·상태·마감일·메타데이터를 유지합니다.
  파일을 다시 읽지 못해 입력 해시가 달라져도 기존 요약을 지우지 않습니다.
  최초 실패만 `failed`, `result=NULL`로 저장합니다.
- 보정 실패: 첫 응답이 형식 검사를 통과했다면 버리지 않습니다. 기존 공개 요약이
  있으면 유지하고, 없으면 첫 요약과 네 카드를 `needs_review`로 저장합니다.
- `superseded`: 더 최신 실행 또는 원문 변경으로 저장이 거절된 실행 결과입니다.
  현재 DB 행을 다시 조회하거나 최신 원문으로 입력을 준비해야 합니다.

job은 Gemini 호출 전에 실행 토큰을 등록하고, 완료 시 토큰과 원문 버전이 일치할 때만
저장합니다. `storage.summary_source.load_summary_source()`는 원문·파일·버전을 한 번에
읽습니다. 이 결과로 입력을 준비하고 `source.content_revision`을 필수 인자
`expected_source_revision`에 그대로 전달합니다. 버전 누락·잘못된 값은 DB 등록과
Gemini 호출 전에 거부하고, 원문이 이미 바뀌었으면 `superseded`로 종료합니다.
준비 후 현재 버전만 다시 조회해 전달하면 이전 입력을 보호할 수 없습니다.
원문·파일이 바뀌면 기존 공개 결과·카드·분류·
마감일은 즉시 비워지고, 기존 `summarized`는 결과 없는 `needs_review`로 바뀝니다.
반복 수집이나 공개 여부만 바뀌는 경우에는 버전이 증가하지 않습니다.
실행 토큰 없는 저수준 저장은 기존 해시 비교 방식을 유지하므로 job 연동을 사용하세요.

API 호출 중 DB 잠금을 유지하지 않도록 autocommit 연결을 권장합니다.
`attempt_count`는 저장이 적용된 요약 실행의 누적 횟수이며 성공 후 초기화하지 않습니다.
내부 재요청·HTTP 전송 횟수와 다르며 기본 job의 `superseded` 종료는 세지 않습니다.
외부에서 같은 실행의 `pending`을 `attempt_increment=1`로 기록했다면 job에는 `attempt_increment=0`을 전달합니다.

### 같은 원문의 재요약에서 정보 보존 (#40)

응답이 형식 검사를 통과해도 같은 원문의 기존 정보를 지우는 후보는 자동 교체하지 않습니다.
DB의 UPSERT 행 잠금 안에서 기존 행과 후보를 비교하며 다음 중 하나라도 해당하면 보존합니다.

- 기존 대상·행동·장소·발행처·지역·분류 등 채워진 값이 빈 값이나 `unknown`으로 바뀜
- 기존 대상·기한·행동·유의사항 카드가 비어짐
- 일정 종류별 항목 수나 시작일·종료일·시각의 채워진 값 수가 줄어듦
- 유의사항·주제 수, 근거가 연결된 필드 집합이 줄어듦(해소된 불확실성의 근거 제외)
- 기존 정렬 마감일 또는 파일 연결 manifest가 사라짐

보존할 때는 기존 결과·카드·상태·마감일·근거·파일 연결·생성 메타데이터를 **통째로** 유지합니다.
새 후보의 일부 필드를 기존 결과와 섞지 않습니다. `attempt_count`, `updated_at`과 비공개
`last_error_code='summary_information_loss'`만 갱신하며, 이 코드는 자동 재시도 지시가 아닙니다.
반환값의 `information_loss_prevented=True`로 이 결정을 구분할 수 있습니다. 반환값의 `result`는
생성된 후보이므로, 앱은 commit 이후 DB를 다시 조회해야 합니다. 반환되는 `status`,
`deadline_on`, `generated_at`은 실제 저장된 행의 값입니다. 보정·첨부 준비 실패에 따른
기존 결과 보존은 별도 처리이며 이 플래그로 표시하지 않습니다.

기존 결과가 없는 최초 부분 요약은 저장할 수 있고, 정보가 줄지 않는 문구·날짜 수정이나
정보 보완도 허용합니다. 원문이 바뀌면 기존 버전 무효화 규칙을 따릅니다. 실행 토큰이 있는
경로는 원문 버전을 기준으로 판단하므로 입력 해시만 달라져도 보호합니다. 토큰 없는 기존
저수준 호출은 해시가 같은 경우만 보호하므로 실제 연동은 위 job 경로를 사용하세요.

이 정책은 정보가 채워진 범위를 비교하며 문장의 의미·정확도까지 판정하지는 않습니다.
같은 개수의 항목을 다른 내용으로 바꾸거나 기존 오류를 삭제해야 하는 상황은 별도 검토가
필요합니다. 검토자용 강제 교체 기능은 포함하지 않습니다.

코드가 DB 함수 `summary_information_loss`를 호출하므로, 이 함수를 만드는
`supabase/migrations/20260922053902_notice_summaries.sql`을 코드보다 먼저 적용합니다. 자동 검증에서는 임시 PostgreSQL에만 적용하며 공식 DB 적용은 별도입니다.

### 분야 코드

기존 `category`는 공지 유형이며, `category_code`는 아래 분야의 **정수**입니다.
분야 근거는 `evidence.field='category_code'`로 연결합니다. 확인 불가는 NULL입니다.

| 코드 | 분야 | 코드 | 분야 |
| --- | --- | --- | --- |
| 21 | 교통 | 25 | 환경 |
| 22 | 안전 | 26 | 문화 |
| 23 | 주택 | 27 | 복지 |
| 24 | 경제 | 30 | 행정 |

## 화면용 4개 요약 카드

Gemini는 기존 원문 근거용 필드를 유지하면서 `card_summaries`의 네 문구를 별도로
작성합니다. 각 값은 한 줄 문자열 또는 `null`이며, 새 카드 문장은 자연스러운
해요체로 끝납니다. 원래 `audience`·`dates`·`action`·`notes`뿐 아니라 장소와
변경·취소·상태 안내가 있으면 관련 카드 문구도 필요합니다. 실제 빈 항목은 비워 둡니다.
카드에만 있고 원문용 필드에 없는 주장은 내용을 보존한 채 검토 대상으로 분류합니다.
명시된 일정 역할별 시작·마감 날짜와 시각을 비교하고, 명확히 변경된 비용은 변경 후
금액을 기준으로 확인합니다. 일부 프로그램의 신청·접수 안내 누락은 기존 보정 1회
예산 안에서 함께 요청합니다. 단순 문의나 신청 불필요는 의무로 추측하지 않습니다.
소식 공지의 기본 `action_requirement='none'`만으로 할 일 문구를 요구하지 않습니다.
파일 전용 공지의 제목은 한 줄 요약·분류에만 사용하며 대상·일정의 검증 근거로 삼지 않습니다.
한 줄 요약은 기존 40자 `summary`를 사용하며, **화면 표시 순서는 미정**입니다.

| 슬롯 | 제목 | 함께 정리하는 원본 정보 |
| --- | --- | --- |
| `audience` | 대상 | `audience`, `audience_scope` |
| `deadline` | 기한 | `dates`의 일정 종류·날짜·시간 |
| `action` | 할 일 | `action`, 필수·선택·권장 구분, `location` |
| `notes` | 유의사항 | `notes`, `changed_details`, `status_detail` |

`notice_summaries.card_summaries`는 `result`에서 자동 생성되는 조회용 컬럼이므로
별도로 쓰지 않습니다. 원본 필드와 `evidence`는 카드의 **원문 근거 보기**에 사용합니다.
이전 결과에 카드 문구가 없으면 원본 필드로 표시하고, 정보가 없는 카드는
**“원문을 확인해 주세요”**로 안내합니다.

앱 응답은 **저장된 DB 행**을 `storage.summary_view.build_notice_summary_view()`에
전달해 만듭니다. job의 일시적인 실패 반환값을 저장된 상태 대신 사용하지 않습니다.

```python
from pipeline.storage.summary_view import build_notice_summary_view

view = build_notice_summary_view(
    status=row["status"],
    result=row["result"],
    attachment_status=row["attachment_status"],
    file_references=row["file_references"],
    notice=notice_input_for_this_row,  # 텍스트 강조가 필요할 때만 전달
)
payload = view.model_dump(mode="json")
```

결과가 있는 `needs_review`도 한 줄 요약과 네 카드를 그대로 반환합니다.
`headline.text`에 경고를 붙이지 않으며, 별도 `message`의 표시 여부는 앱에서 결정합니다.
`result=NULL`인 검토 행은 원문 확인 안내만, `pending`·최초 `failed`는 요약 없이 반환합니다.

### 카드별 텍스트 원문 강조 위치

`notice=`를 전달하면 `text_highlights`에 본문·추출 첨부 텍스트(`sources`)와 카드별
강조 범위(`ranges`)를 제공합니다. 범위는 **반환된 평문**에 대한 JavaScript UTF-16
위치이며 종료 위치는 포함하지 않습니다. 원문 HTML에 직접 적용하지 않습니다.
인용 위치가 여러 곳이면 자동 강조에서 제외합니다. PDF·이미지 좌표는 제공하지 않습니다.
근거는 필드 단위이므로 개별 일정·유의사항까지 검증했다는 뜻은 아닙니다.

### 파일 근거 링크

#13의 `prepare_summary_source()`는 항상 `file_manifest: PrivateSummaryFileManifest`를 제공합니다.
기존 외부 준비기와의 호환을 위해 요약 job 자체에서는 이 필드가 선택적입니다.
`transform.summary_files`의 계약은 원문 URL·조회 당시 `content_revision`, 원본 파일
행별 ID·`file_key`·종류·URL·처리 결과, 실제 전송 블록의 위치·`media_N`·SHA-256을
묶습니다. 추출 텍스트는 `NoticeInput.attachments`의 위치와 텍스트 SHA-256으로 연결합니다.
파일 목록 순서로 연결을 추측하지 않으며, 전송한 내용과 맞지 않으면 호출 전에 거부합니다.
이 계약의 입력은 첫 텍스트 블록이 `render_notice_input(notice)`이고 이후는 파일 블록입니다.
`build_summary_metadata_from_manifest()`는 원본 파일 행별로 읽기 상태를 계산하고,
동일 파일의 추출 텍스트는 `file_key`당 한 번만 입력 해시에 반영합니다.

저장 시 현재 원문 버전과 전체 파일 목록을 대조하며 요약과 연결 정보를 함께 씁니다.
`file_manifest`는 비공개이고 앱은 자동 생성된 `file_references`만 조회합니다.
앱은 `preparation_omissions`도 조회할 수 있습니다. 항목은 `notice_file_id`(본문 이미지는 null),
`url`(등록 파일의 원본 URL 또는 원문 공지 URL), 안전한 `reason_code`만 포함합니다.
모델이 만든 필드가 아니며 해시·파일 키·상세 예외는 노출하지 않습니다.
`build_notice_summary_view(..., preparation_omissions=row["preparation_omissions"])`로
전달하면 미확인 자료 안내와 링크가 응답에 포함됩니다. 화면은 reason code를 사용자 문구로
변환해 표시해야 합니다. 기존 결과를 유지하면 누락 안내도 기존 결과 기준으로 유지하며,
원문 변경으로 결과가 무효화되면 공개 누락 정보도 NULL이 됩니다.
처리 실패나 기존 요약을 유지하는 보정 실패는 기존 링크도 유지합니다.
파일 목록에 없는 본문 이미지는 **“원문에서 확인”**과 원문 공지 링크를 제공합니다.
PDF·이미지 좌표는 제공하지 않습니다. 준비 결과에 연결 정보가 없는 기존 호출은
요약을 유지하며 파일 링크를 만들어내지 않습니다.

현재 한계: 카드의 조건 검사는 모든 의미 오류를 잡아내지 못합니다. `source_hash`에는
제목과 PDF·이미지 바이트가 포함되지 않아 같은 파일 URL의 내용 변경을 감지하지 못할 수
있습니다. 수집·파일 준비에서 이 job으로 이어지는 자동 연결과 모바일 표시·강조는
별도 구현이 필요합니다.

## 공지 ID로 요약 실행

`pipeline summarize-one --notice-id <id>`는 DB에 저장된 공지 하나를 요약해 저장하고, 커밋된 행에서
앱이 읽을 공개 결과를 다시 만들어 JSON 한 줄로 출력합니다. 기존 준비, 검증, 저장 함수를 순서대로
연결할 뿐 새 판정 로직은 없습니다. 쉬운말 변환은 별도 기능(`collect --easy-text`)이며 이 명령의
보고에 섞지 않습니다.

```powershell
python -m uv run pipeline summarize-one --notice-id 123
```

필요한 설정은 `DATABASE_URL`과 `GEMINI_API_KEY`입니다. `GEMINI_API_KEY`가 환경변수에 없으면
`services/pipeline/.env`에서 읽습니다. 둘 중 하나라도 없으면 DB에 아무것도 쓰지 않고 exit code 2로
끝납니다.

### 실행 단계와 트랜잭션 책임

| 순서 | 단계 | DB 연결 |
| --- | --- | --- |
| 1 | `load_summary_source`: 본문, 파일, `content_revision`을 한 문장으로 읽음 | 짧은 autocommit 연결, 읽고 바로 닫음 |
| 2 | `prepare_summary_source`: 첨부 다운로드와 입력 준비 | 연결 없음 |
| 3 | `build_summary_metadata_from_manifest` | 연결 없음 |
| 4 | `summarize_and_save_prepared_notice`: 실행 토큰 등록, Gemini, 결과 또는 실패 저장 | autocommit 연결. 토큰 등록은 즉시 커밋되어 Gemini 호출 중 열린 트랜잭션이 없고, 결과 저장은 저장 함수가 자체 트랜잭션으로 커밋 |
| 5 | `load_stored_summary` → `build_notice_summary_view` | 새 연결로 커밋된 행만 읽음 |

1단계에서 읽은 `content_revision`을 그대로 `expected_source_revision`으로 넘기므로, 준비 중이나
Gemini 호출 중 원문, 파일 목록이 바뀌거나 같은 공지의 더 최신 실행이 시작되면 결과를 저장하지 않고
`superseded`로 끝납니다. 준비 기준 시각(`reference_datetime`)과 저장 시각(`generated_at`)은 모두
`pipeline.clock.now()`에서 얻습니다.

### Gemini 호출 횟수와 비용

- 한 작업의 논리 호출은 최대 2회이며, 형식·내용 보정은 최대 1회입니다. 실제 HTTP 시도도 최대
  2회이고 통신 재시도와 보정이 이 한도를 함께 사용합니다. 첫 호출에서 일시 오류로 HTTP 시도를
  두 번 사용했다면 추가 보정은 보내지 않고 이미 확보한 유효 후보를 기존 보존 규칙으로 처리합니다.
- `gemini_requests`는 논리 호출 수, `gemini_http_attempts`는 HTTP 전송 시도 수입니다.
  `gemini_called`는 HTTP 시도 수가 0보다 큰지 나타냅니다. 전송 전 시간 초과라면 논리 호출이
  있어도 HTTP 시도는 0일 수 있으며, 이 수치는 제공자의 실제 과금 내역을 대신하지 않습니다.
- 공지 없음, 비공개 공지, 읽을 본문과 첨부가 없음, 설정 오류, 이미 오래된 입력이면 Gemini를 호출하지
  않습니다.
- 첨부 PDF와 이미지는 입력 토큰에 포함되어 본문만 있는 공지보다 비용이 큽니다.

### Gemini 전체 시간 예산과 재시도

`GEMINI_EXECUTION_TIMEOUT_SECONDS`는 공지 한 건의 요약 또는 쉬운말 작업에 적용하는 AI 처리
예산이며 기본값은 **120초**입니다. 유한한 양수만 허용하고 빈 값, 0, 음수, `nan`, `inf`는 설정
오류입니다. 두 기능을 독립 작업으로 순서대로 실행하면 기본 AI 처리 예산의 합계는 **240초**입니다.
현재 수집 후 자동 처리는 쉬운말만 실행하며, 요약 자동 실행·재처리 예약은 추가하지 않습니다.

요약의 첨부 준비가 끝난 뒤 예산을 시작합니다. 최초 요청, SDK 시작과 입력 전달, 통신 재시도 대기,
응답 검증과 보정은 같은 monotonic 종료 시각을 사용합니다. 요청마다 120초로 초기화하지 않습니다.
시간 설정은 프로세스 환경 변수를 우선하고, 없으면 로컬 `.env`에서 읽습니다.
기본 AI 예산은 요약 실행 토큰의 DB 등록 이후 시작합니다. 호출자가 전달하거나
이미 활성화된 예산의 종료 시각은 재설정하지 않습니다.
DB 조회·저장과 첨부 다운로드는 이 AI 예산으로 강제 종료하지 않으므로 전체 명령의 실행 시간과는
구분합니다. Python 호출자는 `ExecutionBudget(timeout_seconds=...)`를 전달하거나 같은 작업의
여러 호출을 `execution_budget(...)`로 감싸 예산을 공유할 수 있습니다. 공유한 예산의 HTTP·논리
호출 한도도 누적되므로 요약과 쉬운말을 하나의 기본 예산에 묶으면 두 기능에 각각 2회가 주어지지
않습니다.

Gemini 통신은 DB를 소유하지 않는 자식 프로세스에서 실행합니다. 부모는 종료 정리 시간을 먼저
확보하고 시간 소진 시 프로세스를 종료·회수합니다. Windows의 큰 stdin 전달 중 멈춤도 별도
watchdog가 끊으며, 함수가 반환될 때 이 로컬 통신 작업이 남지 않습니다. 종료 시각 이후 응답은
새 성공 결과로 채택하지 않습니다. 원격 제공자가 이미 접수한 추론의 취소까지 보장하는 것은 아닙니다.
자식이 부모로 전달하는 응답 프로토콜은 UTF-8 기준 4 MiB로 제한합니다. 초과 응답은
`response_incomplete` 실패로 처리하고 기존 결과를 보존합니다.

SDK 자동 재시도는 두 API 모두 끕니다. Interactions의 `attempts=1`은 현재 잠긴 SDK에서
"HTTP 1회"를 뜻하지 않으므로 `gemini_sdk_adapter`에 호환 처리를 격리했습니다. SDK를 올릴 때는
실제 HTTP 횟수를 검증하는 계약 테스트를 함께 실행해야 합니다.

재시도 가능한 오류는 남은 시간과 HTTP 한도가 모두 허용할 때만 재호출합니다.
`Retry-After`의 초·HTTP 날짜와 `retry-after-ms`를 읽으며 여러 값이 있으면 더 늦은 시각을
따릅니다. 서버가 요구한 대기를 줄이지 않습니다. 이번 예산 안에 기다릴 수 없으면 즉시 종료하고
`execution_failure.retry_at`에 UTC 재시도 가능 시각을 전달합니다.

`execution_failure`는 `reason_code`, `failure_kind`(`deadline`, `transient`, `permanent`,
`deferred`), `retryable`, `retry_at`, `status_code`만 담습니다. 원문, 키, HTTP 헤더나 제공자
오류 본문은 포함하지 않습니다. DB에는 기존 오류 코드만 저장하므로 이번 변경에 마이그레이션은
없습니다. 상세 실행 정보·재시도 시각은 실행 결과이며, 재시작 이후에도 예약을 유지할 작업 저장소와
스케줄러는 후속 범위입니다.

보정이 실패해도 같은 원문의 기존 정상 결과를 보존합니다. 기존 결과가 없고 이미 검증한 요약
후보가 있으면 `needs_review`로 보존합니다. 유효하지 않은 쉬운말 응답은 성공으로 저장하지
않습니다. 보존한 결과와 이번 실패를 구분하므로 요약은 `execution_status=failed`와
`stored_status=summarized` 또는 `needs_review`를 함께 반환할 수 있습니다.

수집 결과의 `easy_text`에도 두 요청 카운터와 API 실패별 `execution_failure`가 포함됩니다.
`notice-glossary`는 성공 결과 JSON 형식을 유지하고 실행 카운터를 stderr의 JSON으로 출력합니다.
API 실패 시 stdout에 성공 결과를 출력하거나 기존 결과 파일을 덮어쓰지 않고 stderr에 안전한
실행 실패 정보를 출력합니다.

### 출력 JSON

| 필드 | 의미 |
| --- | --- |
| `notice_id` | 처리한 공지 ID |
| `execution_status` | 이번 실행 결과: `summarized`, `needs_review`, `failed`, `superseded`, `not_found`, `storage_failed` |
| `stored_status` | 실행 후 새 연결로 다시 읽은 `notice_summaries.status`. 행이 없거나 확인하지 못했으면 `null` |
| `public_result` | 앱이 읽을 요약 내용(`view.content`)이 있는지 |
| `attachment_status` | 이번 실행이 읽은 첨부 범위: `none`, `all_read`, `partial`, `unread`. 공지를 찾지 못했으면 `null` |
| `reason_code` | 실패나 검토 사유 코드. 없으면 `null` |
| `gemini_called` | 이번 실행에서 Gemini 요약 요청을 보냈는지 |
| `gemini_requests` | 논리 요약 호출 수 |
| `gemini_http_attempts` | 실제 HTTP 전송 시도 수. 재시도 포함 |
| `execution_failure` | 이번 실행의 안전한 실패·재시도 정보. 없으면 `null` |
| `view` | 커밋된 행으로 만든 공개 응답(`build_notice_summary_view`). 행이 없으면 `null` |

이번 실행이 실패해도 기존 정상 결과가 남아 있으면 `stored_status`, `public_result`, `view`는 그 행을
기준으로 보고합니다. 예를 들어 Gemini 타임아웃이면 `execution_status`는 `failed`, `stored_status`는
`summarized`, `public_result`는 `true`입니다.

| `execution_status` | `reason_code` |
| --- | --- |
| `summarized` | `null` |
| `needs_review` | `attachments_partial`, `attachments_unread`(첨부를 다 읽지 못함), `summary_review_required`(근거, 불확실성 등 요약 판정) |
| `failed` | 이번 실행의 실패 코드. 예: `api_timeout`, `api_error`, `response_validation_failed`, `input_preparation_failed`. 기존 결과를 보존하면 저장된 `last_error_code`와 다를 수 있음 |
| `superseded` | `summary_execution_superseded` |
| `not_found` | `notice_not_found_or_hidden` |
| `storage_failed` | `db_unavailable`(시작 전 연결 실패), `summary_storage_failed` 등 저장 오류 코드, `summary_read_failed`(저장 후 재조회 실패) |

읽을 본문과 첨부가 모두 없는 공지는 입력 준비 단계에서 `no_content` 실패로 처리되어 Gemini 호출 없이
`failed`, `input_preparation_failed`로 기록됩니다.

### exit code

| exit code | 경우 |
| --- | --- |
| 0 | `summarized` 또는 `needs_review`로 저장 완료 |
| 1 | `failed`: 준비 실패, Gemini 실패, 응답 검증 실패가 기록됨 |
| 2 | 인자 또는 설정 오류. DB에 쓰지 않음 |
| 3 | `not_found`: 공지가 없거나 `is_visible = false` |
| 4 | `superseded`: 처리 중 원문 변경 또는 더 최신 실행 시작. 결과를 저장하지 않음 |
| 5 | `storage_failed`: DB 연결, 저장, 재조회 실패. 성공으로 보지 않음 |

### 실패 후 확인할 상태

```sql
select status, last_error_code, attempt_count, attachment_status, generated_at, updated_at
from notice_summaries where notice_id = 123;
```

- `failed`: 기존 정상 결과가 있으면 `status`와 `result`는 그대로이고 `last_error_code`,
  `attempt_count`만 바뀝니다. 일시적인 오류(`api_timeout`, `api_error`)면 다시 실행합니다.
- `superseded`: 원문이 바뀐 경우 기존 요약은 DB 트리거가 이미 무효화했습니다. 다시 실행하면 새 원문으로
  요약합니다.
- `storage_failed`: 결과가 커밋됐는지 확인할 수 없거나 커밋되지 않았습니다. DB 상태를 확인한 뒤 다시
  실행합니다. 같은 원문이면 다시 실행해도 안전합니다.

### 앱이 읽는 결과 (#34)

앱(anon)은 `notice_summaries`에서 `notice_id`, `status`, `category`, `category_code`, `deadline_on`,
`result`, `attachment_status`, `generated_at`, `card_summaries`, `file_references`,
`preparation_omissions`만 읽을 수 있습니다. `source_hash`, `model`, `prompt_version`,
`attempt_count`, `last_error_code`, `file_manifest`는 공개하지 않습니다. 이 명령의 `view`는 같은
행으로 만든 화면용 응답이며 다음 필드를 가집니다.

| 필드 | 내용 | 값이 없을 때 |
| --- | --- | --- |
| `status` | `summarized`, `needs_review`, `pending`, `failed`. 읽지 못한 자료가 있으면 `needs_review` | 항상 있음 |
| `message` | 상태 안내 문구. 예: "원문 확인 요함", "읽지 못한 자료가 있어요. 원문을 확인하세요." | `summarized`면 `null` |
| `content` | `headline`(한 줄 요약), `cards`(`audience`, `deadline`, `action`, `notes`), `metadata` | `pending`, `failed`, 내용 없는 검토 행이면 `null` |
| `text_highlights` | 카드 문장의 원문 근거 위치 | 저장된 행이 이번 실행과 다른 원문에서 만들어졌으면 키가 없음. 내용이 없으면 `null` |
| `file_references` | 근거로 쓴 첨부의 공개 링크 | 행의 값이 NULL이면 키가 없음. 빈 배열 가능 |
| `preparation_omissions` | 읽지 못한 첨부(`notice_file_id`, `url`, `reason_code`) | 행의 값이 NULL이면 키가 없음. 빈 배열 가능 |

이 응답을 읽는 쪽은 위 세 필드의 키가 없는 경우를 빈 값과 같게 처리해야 합니다.

### 재사용 계약

재처리나 예약 실행은 공지마다 `pipeline.summary_run.summarize_one(database, notice_id, *, api_key,
model=DEFAULT_MODEL)`을 호출합니다.

- 반환값 `SummaryRunResult`는 위 출력 필드를 가지며 `report()`가 JSON용 dict, `exit_code`가 위 표의
  값을 돌려줍니다.
- 함수가 자체 연결을 열고 닫으므로 호출자는 트랜잭션을 갖지 않습니다. 환경변수는 읽지 않으니 설정은
  호출자가 확인해 넘깁니다.
- DB 오류는 예외 대신 `storage_failed`로 돌려줍니다. 잘못된 `notice_id`는 `ValueError`, 잘못 저장된
  데이터(검증을 통과하지 못하는 원문 URL 등)와 프로그래밍 오류는 예외를 그대로 올립니다.
- 대상 선택, 예약 실행, 자동 재시도는 이 함수 밖의 별도 작업입니다.

## 수집 후 4카드·쉬운말 자동 처리 (#62)

기본 `collect` / `collect-one`은 **원문만 저장**합니다. `--process-ai`를 주면 원문 수집과
커밋을 모두 끝내고 수집 연결을 닫은 뒤, #61 실행기가 #41 `summarize_one`과 쉬운말 서비스를
독립 실행합니다. 특정 공지의 AI 지연이 같은 수집 호출의 다른 원문 저장을 막지 않습니다.
`--easy-text`는 기존 쉬운말·사전만 실행하는 호환 옵션이며 `--process-ai`와 함께 쓸 수 없습니다.

```bash
# 원문만 수집 (기본)
pipeline collect --source nowon --mode new
# 수집 + 두 AI 자동 처리, 기능별 최대 100건 (기본)
pipeline collect --source nowon --mode new --process-ai
pipeline collect --source wolgye1 --mode refresh --process-ai
pipeline collect --source seoul --source-board 25 --mode new --process-ai
pipeline collect-one --source seoul --source-board 25 --process-ai
# 기능별 처리 상한 조정 (1~10000)
pipeline collect --source nowon --mode refresh --process-ai --processing-limit 20
# 원문을 다시 수집하지 않고 저장된 공지의 누락·실패 후처리 복구
pipeline process-stored --source nowon --limit 20
pipeline process-stored --source nowon --feature easy_text --limit 20
```

DB와 해당 출처 API 설정 외에 `GEMINI_API_KEY`가 필요합니다. 설정 오류는 수집 전에 exit 2로
종료합니다. 사전 후보가 있는 쉬운말에는 기존 #54의 `STDICT_API_KEY`도 사용합니다. 쉬운말 저장이
성공한 뒤 사전 연결을 시도하며 사전 실패가 저장된 AI 결과를 취소하지 않습니다.

새 공지가 0건이어도 **같은 출처의 전체 공개 공지** 중 누락·원문 변경·재시도 시각이 지난 작업을
찾습니다. `collect-one`의 ID도 처리 대상 전체를 제한하지 않습니다. 출처는 nowon / dong(월계1동) /
seoul 단위이며 서울시의 선택한 게시판 외 기존 서울 공지도 재처리 대상입니다. 현재 버전의 정상
캐시는 API를 호출하지 않습니다. 재시도 시각·점유·횟수 제한·늦은 저장 방지는 #61에 맡깁니다.
`blocked`/`exhausted` 수동 복구는 아래 `process-pending --retry` 절차를 사용하세요.

본문 없는 쉬운말은 `skipped/no_body_text`, 요약할 본문·지원 파일이 없는 4카드는 기존 입력
준비기의 `blocked/input_preparation_failed`로 기록하며 성공 생성 건수에 포함하지 않습니다.
파일 전용 요약은 기존 입력 준비기가 판단합니다. 미지원/읽기 실패 파일은 기존 사유 코드와
`preparation_omissions` 또는 실패 상태를 유지합니다. 모든 경우 원문과 파일을 삭제하지 않습니다.

출력의 최상위 `complete`·`saved_count`는 **원문 수집 결과**입니다. `summary`와 `easy_text`는
각각 실제 실행한 `attempted_count`, `succeeded_count`, `skipped_count`, 실패 `records`를 제공합니다.
`readiness`는 해당 출처 전체의 현재 결과/작업 상태 집계입니다. 유효 캐시는 `ready`, 입력 없음은
`skipped`, 처리 상한 초과·재시도 대기·실패는 `pending/retry_wait/blocked/...`로 구분합니다.
남은 작업이 있으면 그 기능의 `complete=false`이므로 API 호출 0회가 생성 완료를 뜻하지 않습니다.
원문 또는 기능·사전 처리가 불완전하면 exit 1, 모두 완료(이유 있는 건너뜀 포함)면 exit 0입니다.
사전은 Gemini 작업과 별도로 현재 쉬운말의 미완료 연결을 조회합니다. 조회 대기 시간이 끝난
재시도 가능 단어와 누락 연결을 최대 `limit`건 처리하며, 영구 실패·대기 중 작업은 호출하지
않습니다. `dictionary_remaining_count`에는 상한 밖·대기·영구 실패도 포함하므로 남아 있으면
완료로 보고하지 않습니다. 완료된 사전 연결은 재작성하지 않습니다.

예를 들어 요약 시간 초과 후 쉬운말만 성공한 경우의 출력 발췌:

```json
{
  "saved_count": 1,
  "complete": true,
  "summary": {
    "complete": false,
    "attempted_count": 1,
    "succeeded_count": 0,
    "retry_wait_count": 1
  },
  "easy_text": {
    "complete": true,
    "attempted_count": 1,
    "succeeded_count": 1
  }
}
```

두 보고서의 `published`는 처리 종료 후 **별도 연결에서 anon 역할로** #58의
`app_notice_detail`을 다시 읽은 최종 `id/display_status/has_easy_text/url`입니다. 프론트 #34는
`app_notice_list` / `app_notice_detail` 계약을 그대로 사용합니다. 결과가 없는 공지도 목록·원문
링크로 접근하며, private 작업 큐나 모델 메타데이터를 공개 view에 추가하지 않습니다.
최종 조회 실패 시 `published=null`, `published_error_code=published_read_failed`와 exit 1을
반환하되, 이미 저장된 결과 및 기능별 실행 건수·기록은 보존합니다.

### backend 적용 순서

이 통합은 #60·#61·#54와 #58 공개 조회 계약을 함께 반영합니다. 서로 다른 브랜치에서
사용한 `20261008150000` 번호 충돌을 해소하기 위해 앱 view의 기존 번호를 유지하고,
사전 캐시 SQL은 내용 변경 없이 `20261008160000_standard_dictionary_cache.sql`로 옮겼습니다.
기존 backend DB에는 이력 조정이 필요할 수 있으므로 [DB 전환 절차](../../supabase/README.md#pr-74-기존-db-전환)를 먼저 확인합니다.
**필요한 migration을 먼저 적용하고 pipeline을 배포**하세요. 기존 공지 `body_text`는
아래 백필 절차 또는 재수집으로 채웁니다. 원격 DB에는 자동 적용하지 않습니다.

GitHub Actions는 기존 `PIPELINE_PRODUCTION_ENABLED=true`일 때만 작동합니다.
활성화된 모든 출처의 원문 수집을 먼저 마친 뒤 `process-stored`를 출처별로 실행합니다.
`PIPELINE_AI_PROCESSING_ENABLED=true`이면 두 AI 기능을, 그렇지 않고
`PIPELINE_EASY_TEXT_ENABLED=true`이면 쉬운말·사전만 처리하며, 둘 다 없으면 원문만 수집합니다.
출처별 요약과 쉬운말·사전은 별도 단계로 실행하며 각각 최대 5건, 20분입니다.
원문 수집은 출처별 10분, 전체 job은 180분으로 제한합니다. 요약의 지연·실패가
쉬운말 단계의 실행 시간을 소진하지 않습니다. 같은 운영 workflow는 동시 실행하지 않고,
진행 중인 실행을 새 실행으로 취소하지 않습니다. 중단된 작업은 #61의 점유 만료 후 복구합니다.
배치 상한 이후 남은 작업·재시도 대기·중단 상태가 있으면 완료로 표시하지 않습니다.
따라서 정상 배치가 일부를 처리했어도 backlog가 남으면 Actions가 실패로 표시될 수 있습니다.

필수 Secrets는 원문 수집용 `PIPELINE_DATABASE_URL`, 노원용 `NOWON_NOTICE_API_KEY`입니다.
서울 수집은 `PIPELINE_SEOUL_ENABLED=true`와 `SEOUL_NEWS_API_KEY`가 필요하며 게시판 25를
처리합니다. 요약 단계에는 `GEMINI_API_KEY`, 쉬운말·사전 단계에는 여기에 `STDICT_API_KEY`가
추가로 필요합니다. 각 단계가 필요한 설정을 검사하므로 AI 키 누락은 원문 수집을 막지 않고,
사전 키 누락은 요약을 막지 않습니다. 실패한 단계 뒤에도 다른 단계는 실행합니다.
두 AI 옵션을 켜도 각 기능은 한 번씩 실행됩니다. 실행 요약 표는 활성화된 단계의
성공·실패·미실행을 집계하며, 비활성화된 기능은 처리 성공을 의미하지 않습니다.
키 값은 출력하지 않습니다. 이 변경은 Repository Variable이나 운영 DB를 변경하지 않습니다.

### 예약 실행 배포·복구 (#63)

- 예약 시간은 UTC 00·04·08시, KST 09·13·17시입니다. 17시는 `refresh`, 나머지는 `new`입니다.
  수동 실행은 같은 workflow에서 `mode`와 `processing_limit`을 선택합니다.
  첫 AI 검증은 기본값 `1`, 이후에는 `5`를 사용합니다. 예약 실행은 `5`입니다.
  상한은 출처별·기능별 작업 수이며 전체 API 호출 수가 아닙니다. 원문 수집량은 바뀌지 않습니다.
- 기본 브랜치 `main`에 반영되어야 예약 실행이 바뀝니다. 이 작업 브랜치는 #62가 통합된
  `backend`에서 시작했습니다. 통합 시 최신 `develop`과의 차이를 검토하고, 선행 코드·migration을
  함께 포함한 `develop` → `main` 배포 PR로 진행합니다. workflow만 옮기지 않습니다.
- 최신 migration 적용 후 `PIPELINE_PRODUCTION_ENABLED=true`로 원문 수집을 활성화합니다.
  `PIPELINE_AI_PROCESSING_ENABLED=true`는 요약·쉬운말·사전, `PIPELINE_EASY_TEXT_ENABLED=true`는
  AI 통합 옵션이 꺼졌을 때 쉬운말·사전만 활성화합니다. 없는 옵션은 비활성으로 취급합니다.
- 전체 중지는 `PIPELINE_PRODUCTION_ENABLED=false`, AI만 중지는 두 AI 옵션을 `false`로 합니다.
  설정 변경은 진행 중 실행을 중지하지 않으므로 긴급 중지 시 Actions 실행도 취소합니다.
  재활성화는 키·DB 상태를 확인한 후 원래 옵션을 복원하고 수동 실행합니다.
- 일시 실패나 시간 초과 후에는 retry 시각·점유 만료 이후 다시 실행합니다. 신규 공지가 없어도
  저장된 공지에서 처리 대상을 찾습니다. 영구 오류·재시도 소진은 아래 #61 절차로 원인을
  해결한 뒤 명시적으로 재시도합니다. 기존 정상 결과를 지워서 재시도하지 않습니다.
- 배포 증거는 수동 실행과 실제 예약 실행 각각의 커밋 SHA·Actions URL·공지 ID·요약/쉬운말
  상태·`anon` 역할의 `app_notice_detail` 조회 결과로 남깁니다. 신규 0건 복구도 확인합니다.
  로컬 테스트나 수동 실행만으로 #63을 완료 처리하지 않습니다.

검증: `tests/e2e/cases/collect_ai_{nowon,wolgye1,seoul}`은 실제 PostgreSQL과 API 대역으로
신규 생성→캐시 재사용→원문 변경·요약 시간 초과→대기→복구→쉬운말 실패→복구 및 anon 조회를
검사합니다. unit/storage는 출처별 점유, 빈 수집, 입력 없음, 기능별 실패 격리와 #60·#61의
시간 제한·경쟁·취소 보호를 검사합니다.

## 누락·실패·원문 변경 후처리 재실행 (#61)

`process-pending`은 이미 저장된 전체 공개 공지에서 현재 4카드 요약 또는 쉬운말 결과가 없는 작업을
선택합니다. 최근 수집 목록이나 신규 수집 건수에 의존하지 않습니다. 기존 `DATABASE_URL`과
`GEMINI_API_KEY`를 사용하며, 위 재처리 마이그레이션을 먼저 적용해야 합니다.

```bash
# API 호출과 DB 변경 없이 대상 확인. GEMINI_API_KEY 불필요
uv run pipeline process-pending --limit 20 --dry-run

# 최대 20개 기능 작업 실행. 공지 하나의 두 기능은 각각 1개로 계산
uv run pipeline process-pending --limit 20
uv run pipeline process-pending --feature easy_text --notice-id 123

# 원인을 해결한 뒤 중단된 특정 작업만 다시 허용
uv run pipeline process-pending --feature summary --notice-id 123 --retry-stopped
```

요약은 `content_revision`, 쉬운말은 제목·본문의 `notice_easy_text_revision`을 사용하며,
모델과 프롬프트 버전도 캐시 계약에 포함합니다. 첨부만 바뀌면 쉬운말 정상 캐시는 유지합니다.
현재 원문의 정상 요약과 내용이 있는 `needs_review`는 재사용하고, 요약과 쉬운말은 독립적으로
선택합니다. 쉬운말은 사전 후보 배열까지 있어야 완료된 캐시입니다. 사전 뜻풀이 조회는 #54 범위입니다.
`process-pending`은 사전 뜻풀이 후처리를 호출하지 않으므로, 이 명령으로 생성한 쉬운말의
사전 조회 RPC는 별도 후처리 전까지 `dictionary_status=pending`을 반환합니다.

비공개 `notice_processing_jobs`의 `(notice_id, feature)` 한 행이 작업 상태를 관리합니다.
앱 역할 `anon`·`authenticated`에는 접근 권한이 없으며 기존 결과 테이블과 공개 형식을 유지합니다.

| 상태 | 의미와 다음 처리 |
| --- | --- |
| `pending` | 아직 실행하지 않은 대상 |
| `running` | 유효한 점유 토큰과 기한이 있는 실행 |
| `retry_wait` | `next_attempt_at` 이후 자동 재시도 가능 |
| `succeeded` | 저장된 결과 확인 완료; 결과가 삭제되면 다시 탐색 |
| `skipped` | 본문 없음 등 처리할 입력이 없어 같은 버전에서는 종료 |
| `blocked` | 응답 검증 실패·정보 손실 정책·확인되지 않은 오류; 원인 확인 필요 |
| `exhausted` | 이번 원문·모델·프롬프트에서 시도 상한 도달 |

`attempts`는 점유한 작업 실행 횟수입니다. Gemini HTTP 요청 횟수와 다르며 실행 도중 프로세스가
사라진 경우도 포함합니다. `--max-attempts` 기본값은 3입니다. 일시 오류는 지수 백오프와 임의 지연을
적용하고, 제공된 `retry_at`이 더 늦으면 그 시각까지 기다립니다. 배치 프로세스가 잠들어 기다리는
방식이 아니라 다음 명령 실행에서 기한이 지난 작업을 회수합니다. 같은 입력의 `blocked`·`exhausted`는
위 수동 재개 명령으로 풀고, 원문 또는 처리 계약이 바뀌면 별도 수동 초기화 없이 다시 평가합니다.
수동 재개도 정상 캐시나 `summary_information_loss` 보호 정책을 우회하지 않습니다.

실행 중에는 DB 트랜잭션을 열어두지 않습니다. 짧은 DB 점유와 만료 회수로 같은 작업을 동시에
점유하지 않으며, 저장 직전에 원문·공개 여부·점유 토큰·기한을 다시 확인합니다. 늦게 끝난 이전
작업은 결과와 최신 작업 상태를 덮어쓸 수 없습니다. 결과 저장 직후 작업 상태를 남기지 못하고
중단돼도 회수 시 정상 캐시를 확인하여 중복 Gemini 호출을 피합니다.

기본 `--job-timeout-seconds 180`, `--lease-seconds 240`입니다. #60의 기본 Gemini 제한 120초보다
길게 두어 구조화된 실패를 반환하고 저장할 시간을 확보합니다. Gemini 제한을 늘리는 경우에도
전체 작업 제한 안에 첨부 준비·저장 여유를 남겨야 합니다. 점유 기한은 작업 제한 시간보다
30초를 초과하여 길어야 합니다. 별도 프로세스에서 첨부 준비부터 저장까지 실행하고, 시간 초과에는
자식 프로세스를 정리합니다. 강제로 끊긴 작업은 서버의 Retry-After를 확인할 수 없으므로
`blocked/job_timeout`으로 남깁니다. 원인을 확인한 후 수동 재개합니다.

**선행 작업과 운용 범위:** #60의 구조화된 실패 정보(`reason_code`, `retryable`, `retry_at`)를
전달받으면 해당 시각과 분류로 재시도합니다. 그 계약이 없는 기존 코드의 불명확한 `api_error`는
자동 재시도하지 않습니다. #26의 원문 변경 후 이전 정상 결과 보존은 별도 저장 계약입니다.
현재 기반 브랜치의 원문 변경 trigger는 이전 결과를 무효화하므로, 원문 변경 중에도 이전 결과를
계속 표시한다는 요구까지 충족하려면 #26을 통합해야 합니다. 동일 원문 재처리 실패는 기존 정상
결과를 보존합니다. 수집 직후 자동 연결(#62)은 같은 실행기를 사용합니다. 별도 예약 복구(#63)는 이 명령을 사용할 수 있습니다.

출력 JSON은 선택·점유·성공·보류·실패 건수와 작업별 상태, 안전한 실패 코드, 다음 시도 시각을
제공합니다. 종료 코드 0은 이번 실행의 정상 완료, 1은 미완료 작업/실행 실패, 2는 잘못된 설정입니다.
종료 코드 0만으로 아직 대기 중인 전체 DB 작업이 모두 끝났다고 해석하지 않습니다.
`--dry-run`은 조회만 하며 `--retry-stopped`와 함께 사용할 수 없습니다.

짧은 공지에서 `action=null`이어도 `location`이 있으면 행동 카드에는 장소 안내를 작성합니다.
보정 요청은 이 관계를 명시하며, 방문·신청 행동을 새로 만들지 않습니다. 첫 응답의 다른
형식 오류를 고친 뒤 두 번째 응답에 카드 문구 누락만 남으면, 근거 검증을 거친 추출 정보와
나머지 카드를 검토 필요 결과로 보존합니다. 누락 문구는 합성하지 않고 기존 추출 항목으로
표시합니다. 이 경우 보정 실패 기록은 유지하며 정상 처리 성공으로 위장하지 않습니다.
카드 객체 누락·잘못된 문체·필수 필드 누락은 이 복구 대상이 아닙니다.

#77의 공개 공지 재현 자료는 `tests/fixtures/short_notice_action_card.json`입니다. 첫 응답은
유의사항 길이 초과, 두 번째는 장소가 있는데 행동 카드가 null인 응답입니다. 이미
`response_validation_failed`로 `blocked`인 공지는 수정 배포 후 원인을 확인하고 위의
공지별 `--retry-stopped` 명령으로 재처리합니다. 검증 실패 전체를 자동 재시도하지 않습니다.


첨부만 있는 공지에서 다운로드 타임아웃·연결 오류·429·5xx로 입력을 얻지 못하면
`attachment_download_failed`로 `retry_wait`에 기록합니다. 기존 대기 간격과 최대 시도 횟수를
따르며, 반복 실패는 `exhausted`로 끝납니다. 실제 빈 입력·미지원 형식·404 등 영구 오류는
자동 재시도하지 않습니다. 본문이 있으면 기존처럼 누락 안내를 포함한 부분 요약을 생성합니다.

#75 수정 전에 `blocked`로 저장된 작업은 자동으로 풀리지 않습니다. 해당 공지의 다운로드
실패 원인과 파일 서버 복구를 확인한 후, 위의 `--notice-id 123 --retry-stopped` 예시처럼
해당 공지만 재실행합니다. 이 옵션은 중단 상태의
시도 횟수를 초기화하므로 전체 공지에 반복 실행하지 않습니다. 기존 요약이나 원문을 삭제할
필요는 없습니다.

운영자는 서버 권한으로 중단 사유와 다음 실행 시각을 조회할 수 있습니다.

```sql
select notice_id, feature, state, attempts, last_error_code, next_attempt_at, lease_expires_at
from public.notice_processing_jobs
order by updated_at, notice_id, feature;
```

## 표준국어대사전 조회와 공유 캐시 (#53)

`pipeline.glossary.dictionary_service.lookup_dictionary()`는 검색용 표제형인
`query_word`를 받는 독립적인 백엔드 함수다. #52의 후보 추출 결과를 입력으로 연결할 수 있다.
공지별 자동 실행·저장, 모바일 조회 API는 후속 연결 작업이며 현재 CLI에서는 호출하지 않는다.
먼저 `20261008160000_standard_dictionary_cache.sql`까지 새 마이그레이션을 적용하고,
서버 환경에 `DATABASE_URL`과 `STDICT_API_KEY`를 설정한다. `.env`는 자동 로드하지 않는다.

```python
from pipeline.config import DatabaseSettings
from pipeline.glossary.dictionary import DictionaryBusy, DictionaryError
from pipeline.glossary.dictionary_service import lookup_dictionary

database = DatabaseSettings.from_env()
try:
    lookup = lookup_dictionary(database, query_word="신청")
except DictionaryBusy as error:
    # 다른 작업의 조회 권한 또는 오류 후 대기 시간이 남아 있다.
    # error.retry_after_seconds 이후 재시도할 수 있다.
    raise
except DictionaryError as error:
    # 오류는 error.code로 구분한다. 요청 URL이나 API 키를 기록하지 않는다.
    raise
else:
    result = lookup.result.model_dump(mode="json")
    # lookup.cache_hit: 기존 DB 결과를 사용했는지 여부

# 명시적인 갱신. 기존 결과가 있어도 새 요청을 수행한다.
# lookup_dictionary(database, query_word="신청", refresh=True)
```

반환 결과는 다음 구조다. 아래 식별자·뜻풀이는 구조 설명용 예시이며 실제 응답을 인용한 것이 아니다.

```json
{
  "query_word": "예시",
  "contract_version": "stdict-v1",
  "status": "found",
  "entries": [{
    "target_code": "100",
    "headword": "예시",
    "homonym_number": "1",
    "source_url": "https://stdict.korean.go.kr/search/searchView.do?word_no=100",
    "senses": [{
      "sense_code": "1001",
      "pos_code": "1",
      "part_of_speech": "명사",
      "definition": "사전에서 제공한 뜻풀이가 들어가는 자리."
    }]
  }]
}
```

- 검색은 `advanced=y`, `target=1`, `method=exact`, `pos=0`으로 표제어 일치를 요청한다.
  검색의 모든 표제어에 상세 조회를 수행해 실제 `sense_code`를 확보한다. 여러 표제어·품사·뜻을
  모두 보존하며 첫 결과를 해당 공지 문맥의 정답으로 선택하지 않는다. `not_found`는 `entries=[]`다.
- 검색어는 Unicode NFC와 앞뒤 공백만 정규화한다. 내부 공백·어미·대소문자는 바꾸지 않는다.
  정규화 검색어·검색 조건·계약 버전의 SHA-256이 공유 캐시 키이며 공지 ID는 포함하지 않는다.
- 검증된 `found`와 `not_found`는 TTL 없이 재사용한다. `refresh=True`로 성공한 경우만 교체한다.
  갱신 중이거나 갱신에 실패해도 일반 조회는 마지막 정상 캐시를 반환한다. 캐시 적중에는 API 키가 필요 없다.
- 짧게 커밋한 DB 트랜잭션에서 180초짜리 조회 권한을 얻고 **연결을 닫은 뒤** HTTP를 호출한다.
  다른 작업은 HTTP를 보내지 않고 `DictionaryBusy`를 받는다. 작업 중단 후에는 권한 만료 시 재처리할 수 있다.
  저장·실패 기록 모두 토큰과 DB 시각의 만료 여부를 다시 검사하므로 이전 작업은 새 결과·권한을 바꿀 수 없다.
  이 180초는 작업 권한의 만료 시간이며 결과 캐시의 유효기간이 아니다.
- HTTP 요청 제한은 10초, 전체 조회 성공 기한은 120초다. 응답당 2 MiB·조회당 16 MiB,
  최대 1,000개 표제어·표제어당 1,000개 뜻을 허용한다. 일부 페이지·뜻을 못 읽거나 상한을 넘으면
  불완전한 결과를 캐시하지 않는다. 리다이렉트는 따라가지 않는다.
  동기 HTTP 클라이언트의 읽기 제한은 수신 간격 기준이다. 서버가 헤더를 계속 조금씩 보내면
  반환 시점이 120초를 넘을 수 있으며, 성공 기한 검사는 다음 응답 처리 시점에 수행한다.
  초과한 결과와 만료된 권한의 결과는 저장하지 않는다. 프로세스 자체의 강제 종료 상한은 아니다.

| 오류 코드 | 자동 HTTP 재시도 | 서비스에서 다음 조회까지 대기 |
| --- | --- | --- |
| `dictionary_timeout`, `dictionary_connection_error`, `dictionary_upstream_error` | 요청별 최대 1회, 전체 기한 안에서 | 5초 |
| `dictionary_rate_limited` | 없음 | 60초 |
| `dictionary_authentication_failed`, `dictionary_missing_api_key`, `dictionary_invalid_request` | 없음; 키·입력 확인 필요 | 60초 |
| `dictionary_invalid_response`, `dictionary_result_limit`, `dictionary_lookup_failed` | 없음; 응답·상한·구현 확인 필요 | 60초 |
| `dictionary_storage_error`, `dictionary_lease_lost` | HTTP 자동 재시도 없음 | DB 복구·권한 만료 후 재호출 |

대기 시간은 실패한 새 조회의 연속 호출을 줄이는 장치다. 정상 캐시가 있으면 즉시 재사용한다.
오류는 `not_found`로 저장하지 않고 고정 코드만 기록한다. API 키는 서버 설정에서 읽고,
반환 객체·DB에는 넣지 않으며 요청 중 현재 스레드의 HTTP 진단 로그를 차단한다.
호출자가 주입하는 HTTP 클라이언트의 임의 이벤트 훅·트레이싱에도 키가 기록되지 않도록 설정해야 한다.

[공식 API 안내](https://stdict.korean.go.kr/openapi/openApiInfo.do)와 실제 응답을 함께 확인했다.
2026-10-08 실제 `신청` 조회에서 표제어 4개·뜻 7개와 항목·뜻 식별자를 확보했다.
같은 날 실제 API와 임시 PostgreSQL을 연결한 서비스 검증에서 `신청`은 최초 HTTP 5회,
미등록 단어는 최초 2회가 발생했으며 둘 다 두 번째 조회에서는 추가 요청 없이 DB 캐시를 반환했다.
상세 API는 `type_search=view` 없이 HTTP 200 빈 본문을 반환하여 해당 값을 명시한다.
미등록 단어는 JSON이 `{}`여서, 같은 조건의 XML 응답에 `total=0`이 명시된 경우에만
`not_found`로 확정한다. 빈 본문·손상 응답을 검색 결과 없음으로 취급하지 않는다.

검증 위치: 클라이언트의 입력·응답 경계는 `tests/unit/easy_text/test_dictionary*.py`,
독립 DB 연결을 사용하는 경합·복구·서비스 연결은 `tests/unit/storage/test_dictionary*.py`,
테이블 제약·앱 접근 차단은 `supabase/tests/test_dictionary_cache_schema.py`다.
서비스의 DB 검증은 `PIPELINE_TEST_DATABASE_URL`로 지정한 로컬 테스트 서버에서 고유 DB를 생성해 수행한다.
자동 테스트는 외부 API와 운영 DB를 사용하지 않는다. 기존 CI가 새 파일을 포함해 실행한다.

2026-10-08 최신 `backend` (`d469c71`) 통합 후, 빈 DB에 초기 스키마 4개와 사전 캐시 1개를
적용해 검증했다. 단위·저장소 3,658개, E2E 41개, 스키마·권한 603개로 **총 4,302개 통과,
0 skipped**. 각 JUnit 결과를 기존 `check_test_report.py`로 확인했고 Ruff도 통과했다.
기존 E2E의 48개 단계 기대값에는 새 테이블의 행 수 `standard_dictionary_cache: 0`만 추가했다.
사전 조회가 연결되기 전 기존 CLI 흐름에서 캐시를 쓰지 않는 상태를 계속 검사한다.

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
python -m uv run pipeline summarize-one --notice-id 123
python -m uv run ruff check
python -m uv run pytest
```

공식 설치 프로그램으로 `uv` 실행 파일이 `PATH`에 등록된 환경에서는 위 명령의
`python -m uv`를 `uv`로 줄여 실행할 수 있습니다.

테스트는 `tests/unit/` 아래 영역별 폴더(`attachments`, `collect`, `easy_text`, `storage`,
`summary`, `tooling`)에 있습니다. 개발 중에는 `python -m uv run pytest tests/unit/collect`처럼
해당 영역만 실행할 수 있습니다. 여러 테스트 파일이 함께 쓰는 helper와 fixture는
`tests/support/`에 두고, 테스트 파일끼리는 서로 import하지 않습니다. 경로는
`support.paths`의 `TESTS_DIR`, `FIXTURES_DIR`, `PIPELINE_DIR`, `REPO_ROOT`를 사용합니다.
`tests/unit/legacy_flow/`는 수집에서 저장까지의 흐름을 확인하는 테스트의 임시 위치이며,
같은 내용을 e2e 케이스로 대체한 뒤 삭제합니다. 남길 테스트의 기준은 CONTRIBUTING.md를 따릅니다.

`tests/unit/storage/test_summary_field_preservation.py`는 #40의 대상·기한 누락 조합,
첨부 링크 보존, 최초 부분 결과와 정상 교체, 동시 실행을 검증합니다. 실제 PostgreSQL에
commit한 뒤 별도의 익명 연결에서 공개 결과를 읽습니다. Gemini와 다운로드 HTTP 응답만
대체하며 실제 외부 서비스의 요약 품질은 검증하지 않습니다. 전체 마이그레이션이 적용된
임시 로컬 DB를 `PIPELINE_TEST_DATABASE_URL` 또는 `AUDIT_TEST_DATABASE_URL`로 지정해야 합니다.

CI는 임시 PostgreSQL에 전체 마이그레이션을 적용해 파이프라인의 DB 검사를 실행하고,
별도의 빈 DB에서 스키마·권한을 검증합니다. 검사 결과가 없거나 건너뛴 검사가 있으면
실패 처리합니다. `scripts/prepare_test_databases.py`는 CI 전용 DB 이름과 로컬 연결을
사용하며, 이미 있는 DB를 초기화하지 않습니다. CI는 `tests/unit`, `tests/e2e`,
`supabase/tests`를 각각 실행해 세 보고서 모두 skip이 없는지 확인합니다. 이 세 폴더 밖에 있는
`test_*.py`는 CI에서 실행되지 않으므로, `tests/unit/tooling/test_ci_database_checks.py`가 `ci.yml`의
`pytest` 대상과 `tests/` 아래 테스트 파일을 비교해 빠진 파일이 있으면 실패합니다. 새 테스트 폴더를
만들 때는 `ci.yml`에 실행 단계를 함께 추가합니다.

## e2e 검증

`tests/e2e/`는 API 응답 예시 파일을 넣고 실제 CLI(`pipeline.cli.main`)를 그대로 실행해,
DB에 저장된 결과와 앱(anon)이 조회하는 결과를 기대값과 비교합니다. 외부 HTTP 요청과
Gemini 호출만 pytest monkeypatch로 녹화 응답에 연결하고, 운영 코드(`src/`)에는 테스트용
분기가 없습니다. 실제 네트워크, 실제 Gemini, 운영 DB는 쓰지 않습니다.

### 로컬 실행

로컬 PostgreSQL 서버 주소를 `E2E_TEST_DATABASE_URL`로 지정합니다. DB 이름은
`pipeline_e2e_test_`로 시작해야 하며, 그 DB 자체는 없어도 됩니다. 케이스마다 같은 서버에
`pipeline_e2e_test_auto_<uuid>` DB를 새로 만들어 마이그레이션을 적용하고, 끝나면 지웁니다.
설정이 없으면 건너뛰지 않고 실패합니다.

```powershell
$env:E2E_TEST_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:5432/pipeline_e2e_test_local"
python -m uv run pytest tests/e2e                     # 전체 케이스와 하네스 검사
python -m uv run pytest tests/e2e -k nowon_api_error  # 케이스 하나
```

실행할 때마다 케이스별 결과가 `artifacts/e2e/<케이스명>.md`에 남습니다(Git 제외). step별 CLI 인자,
exit code, 외부 요청 순서, Gemini 호출 수, 테이블별 행 수, 저장된 공지, anon에 보이는 공지,
기대값과의 차이를 적고, 통과하지 못한 실행에서도 씁니다. CI에서는 `e2e-reports` artifact로 올립니다.

### 케이스 추가 절차

1. `tests/e2e/cases/<케이스명>/input/`에 API 응답 원본(XML, HTML), 첨부 파일, Gemini 응답 JSON을
   넣습니다. 실제 공지를 쓸 때는 첨부 파일명과 본문의 개인 이름, 연락처를 가짜 값으로 바꿉니다.
2. `case.json`에 실행 단계를 적습니다. 노원구 응답은 scaffold로 초안을 만들 수 있습니다.
   ```powershell
   python -m uv run python tests/e2e/harness/scaffold.py --name <케이스명> --source nowon `
       --api <목록.xml> --page <원문.html>
   ```
   scaffold는 수집기가 요청할 URL을 라우트로 채우고, 남은 할 일을 `TODO:`로 출력합니다.
   마스킹된 이름(`이0진` 등)이나 휴대전화 번호가 보이면 경고합니다.
3. 기대값을 생성합니다.
   ```powershell
   $env:E2E_UPDATE = "1"; python -m uv run pytest tests/e2e -k <케이스명>; Remove-Item Env:E2E_UPDATE
   ```
4. 생성된 `expected/step-<n>.json`이 의도와 맞는지 읽고 확인합니다.
5. 커밋합니다. 이후 실행은 기대값과 다르면 테이블, 행 키, 컬럼 단위로 차이를 보여 주며 실패합니다.

`E2E_UPDATE`는 `CI` 환경변수가 있으면 거부됩니다. 실패한 케이스는 동작이 바뀐 이유를 설명할 수
있을 때만 기대값을 갱신하고, PR 본문에 `expected/` 차이를 요약합니다.

### case.json

```json
{
  "title": "이 케이스가 확인하는 것",
  "strict_unused": true,
  "steps": [
    {
      "type": "collect",
      "args": ["--source", "nowon", "--mode", "new"],
      "http": [
        {"route": "nowon_api", "start": 1, "end": 50, "body": "input/nowon_list.xml"},
        {"route": "nowon_page", "post_sn": "900101", "body": "input/nowon_page_900101.html"}
      ],
      "gemini": {"easy_text": [], "summary": []},
      "expect_exit": 0
    }
  ]
}
```

| step `type` | 실행 |
| --- | --- |
| `collect`, `collect-one` | `pipeline <type> <args>`. stdout의 JSON 보고서와 stderr를 스냅샷에 넣습니다 |
| `sql` | 테스트 DB에 `sql`을 직접 실행합니다. 운영자 숨김처럼 코드 경로가 없는 상황에만 쓰고 `reason`을 적습니다 |

| `route` | 필요한 값 | 요청 |
| --- | --- | --- |
| `nowon_api` | `start`, `end` | 서울 열린데이터 `NowonNewsNoticeList/<start>/<end>/` |
| `nowon_page` | `post_sn` | 노원구 공지 원문 `BD_selectBbs.do?q_bbsCode=1001&q_bbscttSn=<post_sn>` |
| `wolgye1_list` | `page`(기본 1) | 월계1동 게시판 목록 |
| `wolgye1_detail` | `post_sn` | 월계1동 게시글 |
| `seoul_api` | `start`, `end`, `board`(선택) | 서울시 `SeoulNewsList` |
| `file` | `url` | 첨부, 본문 이미지의 정확한 URL |

- 응답은 `body`(케이스 폴더 기준 파일 경로) 또는 `text`로 주고, `status`(기본 200)와 `headers`를
  덧붙일 수 있습니다. `Content-Type`은 파일 확장자로 정합니다.
- 같은 요청에 라우트를 여러 개 등록하면 등록 순서대로 하나씩 응답하고, 마지막 라우트는 이후 요청에도
  계속 응답합니다. 예를 들어 503과 200을 차례로 등록하면 첫 요청은 503, 재시도부터는 200을 받습니다.
  라우트를 하나만 등록하면 모든 요청에 같은 응답을 돌려줍니다. 재시도 횟수는 스냅샷의 `http_calls`로
  확인합니다.
- 등록하지 않은 요청은 `Unexpected external URL`로 실패합니다. 등록했지만 요청되지 않은 라우트는
  보고서에 경고로 남고, `strict_unused`가 `true`면 실패합니다.
- API 키는 `test-only-key`로 고정되며 보고서에는 `<key>`로 표시합니다. stdout과 stderr에서는 DB URI,
  `:비밀번호@`, `password=비밀번호` 형태와 API 키를 `[REDACTED]`로 가립니다. 비밀번호 문자열 자체는
  가리지 않아, 로컬과 CI의 비밀번호가 달라도 기대값이 같습니다.
- Gemini 응답은 종류별(`easy_text`, `summary`) 파일 목록을 순서대로 소비합니다. 응답이 모자라거나
  남으면 실패합니다. 쉬운말 처리는 Gemini 예외를 모두 자체 실패로 바꾸지만, 하네스가 응답이 모자란
  호출을 따로 기록하므로 케이스는 실패합니다. 오류를 재현하려면 `{"__error__": "timeout"}`처럼
  적습니다(`timeout`, `connection`, `api_error`, `too_large`).
- 재시도와 요청 간격의 대기(`sleep`)는 실제로 기다리지 않고 보고서에 기록만 합니다.
- `now`(요약 기준 시각)는 요약 step과 함께 Phase B에서 사용합니다.

### 기대값 정규화

- 행은 `category/source_board/post_sn` 공지 키로 묶습니다. `notice_files`는 `공지 키|kind|file_key`입니다.
  `id`, `notice_id`는 기록하지 않습니다.
- 시각 컬럼은 처음 나타나면 `"<set>"`, 이전 step에도 있던 행이면 `"<changed>"` 또는
  `"<unchanged>"`로 적습니다. UUID와 `execution_token`은 `"<set>"`입니다.
- 300자를 넘는 텍스트는 길이와 sha256 앞 16자로 적습니다. 원문은 보고서의 스냅샷 전체에서 볼 수
  있습니다.
- `row_counts`에 공개 스키마 모든 테이블의 행 수를 적어, 스냅샷 대상이 아닌 테이블의 변화도 잡습니다.
- `anon`은 같은 DB에서 `set local role anon`으로 앱 권한과 같은 컬럼만 조회합니다. 앱이 실제로 읽는
  `app_notice_list`, `app_notice_detail` view의 결과도 같은 공지 키로 기록합니다. 마이그레이션이
  anon의 컬럼 권한을 바꾸면 하네스(`harness/snapshot.py`의 `ANON_COLUMNS`, `APP_VIEWS`)가 실패하므로,
  목록과 기대값을 함께 확인합니다.


### 원문이 없는 노원구 공지 (#71)

노원구가 명시적으로 게시물 없음 응답을 반환하면 `source_page_missing`을
`skipped`에 기록합니다. `skipped_count`는 건너뜀 건수이고 `failed_count`는
공지별 실제 실패 건수입니다. `collect-one`, 전체 수집, `new`/`refresh`에 적용합니다.
본문이 비어 있거나 첨부만 있다는 이유로 건너뛰지는 않습니다.

예약 수집은 `selected_count = saved_count + skipped_count + failed_count`입니다.
전체 수집에서는 제한 적용 후 선택한 공지 수가 같은 합계이며, `listed_count`는
제한 적용 전 목록 수입니다. 기존 `attempted_count`는 목록 충돌과 이전 오류로
시도하지 못한 항목을 제외하며, 원문 없음 응답을 받은 항목은 포함합니다.
목록 조회 실패는 `failed_pages`/`failed_ranges`에 별도로 남습니다.

목록 수집이 완료되고 실제 실패나 수동 제한이 없으면, 모두 건너뛴 경우에도
완료로 보고하며 종료 코드 0을 반환합니다. AI 후처리 실패가 있으면 기존처럼
종료 코드 1입니다. 원문 없음 건은 저장·후처리하지 않으며 기존 DB 행의 내용과
공개 상태도 바꾸지 않습니다. 삭제 또는 비공개라고 확정하는 상태는 아닙니다.
기존 Actions는 CLI 종료 코드를 사용하므로 워크플로 변경 없이 적용됩니다.

### 운영 검증 순서 (#63 후속)

1. #76에서 운영 DB의 migration 이력·실제 객체·기존 데이터 보존 확인을 마친다.
   DB 준비 전에는 AI 플래그를 켜지 않는다. #77 수정이 필요한 차단 작업은 수정 후 명시적으로 재개한다.
2. `PIPELINE_DATABASE_URL`, `NOWON_NOTICE_API_KEY`, `GEMINI_API_KEY`, `STDICT_API_KEY`의
   등록 여부를 확인한다. 서울시를 켜려면 `SEOUL_NEWS_API_KEY`도 필요하다.
3. DB 준비 후 `PIPELINE_AI_PROCESSING_ENABLED=true`로 설정하고 검증할 브랜치를 선택해
   `mode=new`, `processing_limit=1`로 수동 실행한다. 이 플래그 변경은 다른 실행에도 적용된다.
   서울시 수집은 지원하는 8개 분야(21~27, 30)를 모두 순회한다. 전체 과거 자료를 한 번에 수집하는 모드는 아니며, 분야별 최초/증분 수집 정책을 따른다.
4. Actions Summary의 커밋·브랜치·기능 활성 상태와 각 단계 JSON의 공지 ID를 기록한다.
   AI 비활성 상태의 녹색 실행을 요약 생성 성공으로 보지 않는다. 성공한 ID의 목록·상세를
   publishable key로 REST 조회하여 저장 결과와 일치하는지 확인한다. SQL Editor의 관리자 조회만으로
   앱 공개 검증을 대신하지 않는다. #34·#35의 실제 Android 화면 검증은 별도로 남긴다.
5. 신규 수집 0건인 후속 실행에서도 누락 작업 복구·정상 캐시 재사용을 확인한다.
   `blocked` 작업은 반복 실행만으로 재개되지 않는다. 기존 결과 삭제로 재처리를 유도하지 않는다.
6. 검증한 코드와 스키마를 포함한 `develop` → `main` PR을 리뷰·병합한 뒤 실제 예약 실행을 확인한다.
   수동/예약 실행 URL, SHA, 공지 ID, 공개 조회 결과를 기록하기 전에는 #63을 완료로 닫지 않는다.

원문 변경 시 이전 버전 결과 보존(#26)은 이번 운영 검증 범위에서 제외한다.
현재 정책에서는 원문 변경 시 이전 요약이 무효화될 수 있다. 같은 원문의 재처리 실패 시 정상 결과 보존은
계속 검증한다. DB migration 적용, 플래그 변경, main 배포는 서로 다른 단계다.
