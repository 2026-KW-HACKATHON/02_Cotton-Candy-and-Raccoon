# 공지 DB — 수집 원문과 생성된 요약

원격 Supabase DB를 만들기 전에, 2026-10-08까지 쌓인 마이그레이션 14개를 기능별 최종 상태의 4개 파일로 재구성했다. 재구성 전후의 스키마는 Supabase식 기본 권한을 재현한 DB에서 `pg_dump -s`로 비교했고, 의도한 차이는 아래 세 가지뿐이다.

- `summary_file_references`, `summary_preparation_omissions`, 원문 변경 trigger 함수 2개의 실행 권한을 `anon`, `authenticated`에서도 회수했다. Supabase는 새 함수에 두 역할의 실행 권한을 따로 주므로 `public`에서만 회수하면 앱이 실행할 수 있었다.
- `notices_id_seq`, `notice_files_id_seq`의 `anon`, `authenticated` 권한을 회수했다.
- `holidays` 테이블 설명을 실제 사용 여부에 맞게 고쳤다.

이 파일들은 **비어 있는 DB의 최초 생성용**이다. 기존 DB를 업그레이드하는 파일이 아니다. 협업 규칙("이미 공유·적용된 마이그레이션은 수정하지 않는다")의 예외이며, 원격 DB가 아직 없어서 가능했다. 이후 스키마 변경은 이 파일들을 고치지 않고 새 마이그레이션으로 추가한다.

재구성 전 마이그레이션 14개를 적용한 로컬 DB는 마이그레이션 이력이 달라 새 파일을 적용하지 못한다(`already exists`). 로컬 Supabase는 `npx supabase db reset`으로 다시 만들고, 테스트용 임시 DB는 지우고 다시 준비한다.

## SQL 파일별 역할과 적용 순서

마이그레이션을 파일명 순서대로 적용한다. 앞의 4개는 최초 생성용이고, 이후 변경은 새 파일로 추가한다. 요약과 쉬운말 파일은 notices 파일에만 의존하고 서로 의존하지 않는다.

| 파일 | 역할 |
| --- | --- |
| [20260922053900_notices.sql](migrations/20260922053900_notices.sql) | 노원구·동·서울시 notices와 notice_files, 식별 키·검증 제약, 앱 조회 정책과 허용 파일 컬럼, service_role 권한 |
| [20260922053901_holidays.sql](migrations/20260922053901_holidays.sql) | holidays 테이블과 앱 접근 차단. 현재 pipeline은 읽지 않는다 |
| [20260922053902_notice_summaries.sql](migrations/20260922053902_notice_summaries.sql) | notice_summaries와 생성 컬럼 함수, 정보 손실 비교 함수, notice_summary_executions, 원문 변경 trigger(`content_revision`, 요약 무효화), 앱 조회 컬럼과 backend 권한 |
| [20260922053903_notice_easy_texts.sql](migrations/20260922053903_notice_easy_texts.sql) | 쉬운말 결과, 버전·제목 보존 검사 함수, 범위 무효화 trigger, 앱 조회 정책 |
| [20261008150000_app_notice_views.sql](migrations/20261008150000_app_notice_views.sql) | 앱 공개 조회 계약(#58): `notices.body_text`, 쉬운말 공개 컬럼 축소, `app_notice_list`·`app_notice_detail` view |
| [seed.sql](seed.sql) | 로컬 개발용 공지·파일과 앱 화면 상태별 요약·쉬운말 데이터. 스키마 변경 SQL이 아님 |

공지 고유 키는 `(category, source_board, post_sn)`, 파일 고유 키는 `(notice_id, file_key, kind)`다.

## 앱 공개 조회 계약 — 이슈 #58

앱(Expo)은 publishable key(anon)로 Supabase REST API를 통해 **view 2개만** 읽는다. view가 앱과 DB 사이의 계약이며, 컬럼 추가는 하위 호환으로 허용하고 이름 변경과 삭제는 FE와 합의한 뒤 새 마이그레이션으로 한다. 전체 설계는 프로젝트 문서 "프론트엔드와 백엔드 API 연결 청사진"의 3장을 따른다.

| view | 용도 | 컬럼 |
| --- | --- | --- |
| `app_notice_list` | 목록, 홈, 보관함 | `id, source, dong_group, is_pinned, title, department, registered_on, content_updated_at, is_modified, summary_status, display_status, notice_type, category_code, deadline_on, headline, card_summaries, attachment_status, has_easy_text` |
| `app_notice_detail` | 상세 | 목록 컬럼 + `url, license_type, body_text, result, generated_at, file_references, preparation_omissions, files, easy_original_text, easy_text, easy_changes, easy_body_text_present, easy_attachment_content_included, easy_generated_at` |

- 두 view는 `security_invoker = true`다. 이 옵션이 없으면 view가 소유자 권한으로 실행되어 RLS를 건너뛰고 숨긴 공지가 보인다. 옵션 덕분에 보이는 공지만, 현재 원문 버전의 쉬운말만 나온다.
- 앱 역할은 두 view를 읽기만 한다. view에 쓰기를 시도하면 PostgreSQL이 "자동 갱신할 수 없는 view"(`55000`)로 거부하며, REST API에서는 HTTP 500으로 보인다.
- 다른 동 글(`dong_group = 'other'`)은 고정 해제 글 숨김 처리 전까지 view에서 제외한다. 테이블 RLS로는 여전히 보인다.
- `display_status`: 요약 행이 없으면 `none`, `summarized`인데 `preparation_omissions`가 있으면 `needs_review`, 그 외에는 저장된 `status`다. Python `build_notice_summary_view`와 같은 규칙이다. pipeline 저장 코드는 검토가 필요한 결과를 `summarized`로 저장하지 않으므로, pipeline이 쓴 행에서는 두 결과가 같다.
- `category_code`가 null이면 미분류다. 요약이 없는 공지도 포함되므로 `category_code=is.null` 한 조건으로 미분류 필터를 만든다.
- `body_text`는 pipeline이 저장할 때마다 `html_to_notice_text(body_html)`로 만드는 평문이다. 요약 입력의 본문 평문과 같은 함수이며, 평문이 비면 null이다. 파생 값이라 원문 변경 trigger의 비교 대상이 아니다. 따라서 `body_text`만 다시 채워도 `content_revision`과 기존 요약은 그대로다.
- `files`는 원본 첨부와 본문 이미지의 `[{id, kind, url}]`이다. 파일 이름은 공개하지 않는다.
- `easy_changes`의 `start`, `end`는 `easy_original_text` 기준 Python 코드포인트 위치다. JavaScript 문자열 인덱스(UTF-16)와 다르다.
- `notice_easy_texts` 테이블의 앱 공개 컬럼은 `notice_id, original_text, easy_text, changes, body_text_present, attachment_content_included, generated_at`으로 좁혔다. `notice_revision, source_hash, model, prompt_version, attempt_count`는 비공개다. 읽기 policy는 `notice_revision`을 참조하지만 policy 식은 컬럼 권한 검사를 받지 않으므로 그대로 동작한다.
- 비공개 컬럼이 있는 테이블(`notice_summaries`, `notice_files`, `notice_easy_texts`)에서 `select=*`는 `42501`로 거부된다. view는 모든 컬럼이 공개 계약이라 `select=*`가 되지만, 앱은 컬럼을 명시해 요청한다.

`seed.sql`의 6~11번 공지는 화면 상태별 확인용이다(`summarized`와 카드·마감일, 첨부 일부 누락, 미분류 검토, 실패, 요약 없음, 이전 본문 기준이라 숨는 쉬운말). 요약과 쉬운말 행은 손으로 쓰지 않고, pipeline `summarize-one`과 쉬운말 처리를 Gemini, 파일 다운로드 대역으로 실행해 저장된 행을 옮겼다. `file_manifest`의 공지와 파일 id는 `db reset`의 삽입 순서로 정해지므로, seed 앞부분의 삽입 순서를 바꾸면 이 구역을 다시 만들어야 한다.

## 공지의 식별 기준

`notices` 고유 키는 `(category, source_board, post_sn)`이다. 세 값은 모두 문자열이다.

| 출처 | category | source_board | post_sn |
| --- | --- | --- | --- |
| 노원구 | nowon | 1001 | API ID |
| 월계1동 게시판 | dong | 1042 | q_bbscttSn |
| 서울시 | seoul | API BLOG_ID | API POST_ID |

서울시 BLOG_ID는 21·22·23·24·25·26·27·30을 허용한다. 분야가 다른 동일 POST_ID는 서로 다른 공지다. 월계1동 목록에 노출되는 다른 동 공지도 게시판 번호는 1042이고 `dong_group=other`로 분류한다. `source_board`는 동 분류가 아니다.

`nowon`과 `seoul`은 `dong_group=NULL`, `is_pinned=false`다. INSERT 시 source_board를 필수로 전달하며 기본값이나 기존 행 backfill은 없다.

## 파일의 식별 기준

`notice_files.notice_id`는 계속 `notices.id`를 참조한다. 새 고유 키는 `(notice_id, file_key, kind)`다.

- 실제 파일 ID가 있으면 `file_key = 'id:' + file_id`.
- ID가 없으면 `file_id=NULL`, `file_key = 'url:' + SHA256(저장할 정규화 URL의 UTF-8 바이트)`의 소문자 16진수 값.
- 실제 그룹 번호가 없으면 `file_sn=NULL`. 가짜 번호·UUID를 만들지 않는다.
- 같은 파일이 첨부이자 본문 이미지이면 kind가 다른 두 행을 허용한다.
- 같은 역할의 중복은 차단한다. file_sn은 중복 가능하다.

URL 해시는 파일 내용 해시가 아니다. URL이 바뀌면 다른 참조로 판단한다. URL 정규화는 저장 코드의 책임이며, SQL은 저장 URL과 file_key가 일치하는지만 검사한다. 최초 INSERT부터 저장 코드가 file_key를 전달해야 한다.

앱의 조회 범위는 기존과 같다. nowon/dong/seoul의 공개 공지는 조회할 수 있고, 파일은 `id, notice_id, kind, url`만 허용한다. `file_name, file_sn, file_id, file_key`는 허용 컬럼이 아니다. RLS에서 숨김 공지와 그 파일을 제외한다. 기본 테이블 권한을 회수하고 읽기 권한만 명시하여 INSERT/UPDATE/DELETE/TRUNCATE를 허용하지 않는다. Holidays는 앱이 접근할 수 없다.

## 요약 저장 계약 — 이슈 #14

부분 요약의 누락 안내는 `notice_summaries.preparation_omissions`로 조회한다. 이 컬럼은 비공개 `file_manifest.omissions`에서
생성되는 읽기 전용 JSON 컬럼이며 앱에 SELECT만 허용한다. 각 항목은 `notice_file_id`, `url`,
`reason_code`로 구성된다. 등록되지 않은 본문 이미지는 파일 ID가 NULL이고 원문 공지 링크를 사용한다.
파일 키·해시·상세 예외는 공개하지 않는다. 기존 결과를 보존하는 재처리는 기존 누락 안내도 보존하고,
원문 변경으로 `result`가 NULL이 되면 이 컬럼도 NULL이 된다. 구버전 manifest의 누락 목록은 빈 배열이다.

부분 요약은 `needs_review`이고 정렬 마감일은 NULL이다. 같은 원문 버전에서 기존 요약이 있으면
새 부분 요약으로 교체하지 않으며 `input_preparation_failed` 코드와 시도 횟수만 기록한다.
전체 입력 준비에 성공한 후 다시 요약하면 결과와 누락 안내를 함께 갱신한다.

`notice_summaries`는 공지당 한 행을 저장하며 공지 삭제 시 함께 삭제된다. `category`는 공지 유형(`application`, `event`, `living`, `obligation`, `news`, `mixed`)이다. `unknown`은 JSON에 보존하고 DB 컬럼은 NULL로 저장한다.

분야 `category_code`는 정수 `21=교통`, `22=안전`, `23=주택`, `24=경제`, `25=환경`, `26=문화`, `27=복지`, `30=행정`이다. 수집 출처인 `notices.category`, 게시판 식별자인 `source_board`와 별개다. 결과 JSON의 분류는 DB 컬럼과 일치해야 하며, 검토 결과의 미확인 분류는 NULL을 허용한다.

| status | 공개 결과 |
| --- | --- |
| `pending` | 공개 요약이 없는 대기 상태 |
| `summarized` | 검증을 통과한 전체 `NoticeSummary`와 evidence |
| `needs_review` | AI 요약·카드를 보존하며 검토 안내는 별도 `message`로 반환 |
| `failed` | 기존 저장 행이 없던 최초 실행 실패 |

`pending`, `failed`의 결과·분류·마감일은 NULL이다. 파일 근거만 확인되거나 첨부를 일부 읽지 못한 결과는 `needs_review`로 보존하며 `deadline_on=NULL`로 둔다. 원문 변경으로 무효화되거나 기존에 내용이 없던 검토 행은 `result=NULL`일 수 있다.

`result.card_summaries`는 `audience`, `deadline`, `action`, `notes` 네 키의 한 줄 문자열 또는 NULL이다. 새 응답에는 객체가 필수이며 기존 JSON의 누락·NULL은 호환한다. 기존 필드와 `evidence`는 원문 근거 보기용으로 유지하고, 직접 인용은 `evidence.excerpt`로 구분한다.

조회용 `notice_summaries.card_summaries`는 `result.card_summaries`에서 자동 생성되는 STORED 컬럼이다. 백엔드는 `result`만 저장하며 카드 컬럼에 직접 쓰지 않는다.

job의 실패는 원문 버전·실행 토큰이 유효하면 `last_error_code`, `attempt_count`, `updated_at`만 갱신한다. 첨부 재처리 실패로 해시가 달라져도 기존 요약을 지우지 않는다. 실제 원문 변경은 DB trigger가 별도로 무효화한다. 토큰 없는 저수준 저장은 기존 해시 비교·무효화 계약을 유지한다.

보정 재요청이 실패해도 형식 검사를 통과한 첫 요약은 보존한다. 기존 공개 결과가 있으면 그대로 유지하고 실패만 기록하며, 없으면 첫 결과를 `needs_review`, `deadline_on=NULL`로 저장한다. 재시도 대상을 선택할 때는 실패 사유를 구분한다.

#40의 `summary_information_loss`는 재시도 지시가 아닌 정보 손실 후보의 교체 거절 사유다.
같은 원문의 채워진 값·카드·일정·근거 범위가 줄면 결과와 파일 연결, 상태, 기한, 생성 메타데이터를
모두 보존하고 코드·시도 횟수·갱신 시각만 기록한다. 판정 함수는 백엔드만 호출할 수 있으며
앱의 조회 컬럼·권한은 바뀌지 않는다. 세부 비교 기준과 반환값은
[파이프라인 저장 계약](../services/pipeline/README.md#같은-원문의-재요약에서-정보-보존-40)을 따른다.
저장 코드가 이 함수를 호출하므로 `20260922053902_notice_summaries.sql`을 코드보다 먼저 적용한다.

`source_hash`는 본문 평문과 읽은 첨부 텍스트를 `file_key` 순으로 구성한 입력의 SHA-256 소문자 64자리다. PDF·이미지 바이트를 포함하는 확장은 후속 작업이다. 모델·프롬프트 버전은 안전한 식별자만, 오류는 코드 목록만 저장한다. `attempt_count`는 누적 실행 횟수이며 성공 후 초기화 정책은 추가하지 않는다.

마감일은 검증된 `dates` 중 `application`, `submission`, `payment`의 `end_date` 최댓값이다. 해당 날짜가 없으면 NULL이며, 검토 상태에서는 마감일 계산을 호출하지 않는다. Gemini가 마감일 DB 컬럼을 직접 정하지 않는다.

`notices.content_revision`은 본문·첨부 메타데이터가 바뀔 때 증가한다. 변경 시 기존 공개 결과·분류·마감일을 즉시 비우고 `summarized`를 `needs_review`로 바꾸며 성공 메타데이터와 시도 횟수는 유지한다. 원문이 같은 재수집은 버전을 올리지 않는다.

job 연동에서는 원문·파일과 같은 DB snapshot에서 읽은 `content_revision`을 `expected_source_revision`으로 **반드시 전달한다**. 비공개 실행 토큰과 원문 버전이 맞는 결과만 저장하며 오래된 실행은 `summary_execution_superseded`로 반환한다. 버전을 생략하는 호환 경로는 등록 전 원문 변경을 보호하지 못한다. API 호출 중 잠금을 유지하지 않으려면 autocommit 연결을 사용하며 commit은 호출자 책임이다.

앱 역할 `anon`, `authenticated`는 보이는 공지의 **11개 컬럼만** 읽는다: `notice_id`, `status`, `category`, `category_code`, `deadline_on`, `result`, `card_summaries`, `attachment_status`, `generated_at`, `file_references`, `preparation_omissions`. 내부 메타데이터·실행 레지스트리와 앱 쓰기는 차단하며 `service_role`에는 백엔드 조회·쓰기를 허용한다.

`file_manifest`는 준비기가 제공하는 원본 파일별 처리 결과와 전송 블록 연결 정보이며 비공개다. 저장 시 원문 버전·URL·전체 파일 ID/키/종류/URL을 대조한다. `file_references`는 파일 ID·종류·URL만 공개하는 generated 컬럼으로 직접 쓰지 않는다. 미등록 본문 이미지는 원문 공지 URL과 “원문에서 확인” 안내를 제공한다. 기존 요약을 유지하는 실패·보정 실패는 연결 정보도 유지하고, 원문 변경으로 `result`가 NULL이 되면 공개 링크도 NULL이 된다.

`notices.content_updated_at`은 신규 공지에 현재 시각을 넣는다. 이후 파이프라인이 실제 본문·제목 등의 내용 또는 파일 목록 변경 시에만 갱신한다. 단순 재수집 시간인 `updated_at`, 수정 이력 표시인 `is_modified`와 역할이 다르다. 앱은 기존 공지 읽기 권한으로 이 새 컬럼을 조회한다.

## BE 호환과 배포 주의사항

새 컬럼에 기본값이 없으므로 **변경 전 BE 저장 코드를 그대로 쓰면 실패한다.** #18 브랜치에서는 아래 1~3을 반영하고 임시 PostgreSQL로 저장을 검증했다. DB와 새 저장 코드를 함께 배포해야 하며, 공식 DB에는 아직 적용하지 않았다.

1. 공지 모델·변환·INSERT·upsert·중복 판단에 source_board를 반영한다.
2. 파일 모델에서 file_sn/file_id를 선택값으로 바꾸고 file_key를 계산·저장한다.
3. 파일 집합 비교와 중복 제거를 file_key + kind 기준으로 맞춘다.
4. Gemini 가공 브랜치의 DB 조회 모델을 nullable 식별자에 맞추고 서울시 다운로드 호스트를 지원한다.

4번 Gemini 가공 연동은 별도 #13 작업이다. notice_id로 파일을 조회하는 관계는 유지된다. #18은 1~3번 저장 계약 연동과 서울시 한 건·분야별 최초 25건·평소 10건 CLI를 구현했다. 현재 서울시는 원문 크롤링 없이 API POST_CONTENT와 그 안의 파일 URL만 저장하며, 본문 밖 별도 첨부는 수집하지 않는다. API에 공지별 공공누리가 없어 license_type은 NULL이다. 예전 발급 키 검증의 공지195·파일430행 및 원문 리다이렉트5건은 정책 변경 전 이력이며 현재 API 전용 검증이 아니다. Actions 예약 연결·실제 Gemini 전송·실제 Supabase 앱 키 조회는 별도 작업이다. 통합 init의 최종 DB 저장 계약은 변경하지 않았으며 공식 DB에는 쓰지 않았다.

## DB 검증

2026-10-08(#58) 마이그레이션 5개와 seed를 적용한 임시 PostgreSQL에서 `test_app_notice_views.py`가 view의 `security_invoker`, 컬럼 계약, 앱 역할 권한, 보이는 공지와 다른 동 제외, 미분류 필터, `display_status`와 Python 결과 일치, 쉬운말 비공개 컬럼 거부를 검증한다. 같은 DB에 PostgREST 12.2.3을 anon으로 띄워 목록, 미분류 필터, keyset 다음 페이지, 상세, 숨긴 공지, `select=*`, 쓰기 거부를 HTTP로 확인했다. Supabase CLI(`npx supabase db reset`)와 호스팅 Supabase에서는 확인하지 않았다.

2026-10-08 마이그레이션 4개와 seed를 Supabase식 기본 권한(테이블, sequence, 함수)을 재현한 임시 PostgreSQL에 적용하는 스키마 테스트가 통과했다. `test_function_and_sequence_privileges.py`가 앱 역할의 함수 실행과 sequence 권한 회수를 검증하며, 재구성 전 마이그레이션에서는 이 검사가 실패한다. 재구성 전 업그레이드 경로(기존 행 backfill) 검사는 업그레이드 경로가 없어져 삭제했다.

2026-10-06 임시 PostgreSQL **17.11**에서 마이그레이션 4개와 seed를 적용하는 스키마 테스트가 **153 passed, 0 skipped**로 통과했다. 이전 공지의 내용 변경 시각 backfill, 요약 상태·JSON 분류 제약, 앱의 실제 역할별 SQL 조회·쓰기 차단, 서비스 역할 권한을 검증했다. Ruff와 diff 검사도 통과했다. 테스트 변경은 롤백했으며 실제 Supabase 프로젝트나 Data API에는 접근하지 않았다. `npx supabase db reset` 성공을 뜻하는 검증은 아니다.

2026-10-04 임시 PostgreSQL17.11에서 **39 passed, 0 skipped**, Ruff와 diff 검사 통과. 기존5개 SQL과 통합3개 SQL의 컬럼28개·제약15개·인덱스·RLS정책2개·RLS활성 상태를 비교해 동일함을 확인했다(컬럼의 물리적 순서는 비교하지 않음). 앱의 테이블 쓰기 권한은 명시적으로 회수했다. Holidays·seed는 diff가 없다. 검증용 임시 서버·DB·로그는 종료/삭제했으며 실제 Supabase 프로젝트 키 조회는 수행하지 않았다.

`supabase/tests/`의 `test_source_identity.py`, `test_notice_summary_schema.py`, `test_notice_summary_execution_schema.py`, `test_notice_summary_source_revision_schema.py`, `test_summary_omissions_schema.py`, `test_function_and_sequence_privileges.py`가 수집·요약·실행·원문 버전·권한 계약을 검증한다. 마이그레이션 4개와 seed를 빈 임시 DB에 파일명 순서대로 적용하며 검증 후 롤백한다. 파일 연결·실패 보존·실제 앱 권한·저장 경합은 파이프라인의 `test_summary_file_manifest_storage.py`에서 검사한다.

services/pipeline 폴더에서 PowerShell로 실행한다.

```powershell
$env:SCHEMA_TEST_DATABASE_URL = 'postgresql://pipeline_test@127.0.0.1:55442/pipeline_schema_test_init'
.\.venv\Scripts\python.exe -m pytest ../../supabase/tests -q -p no:cacheprovider
```

먼저 해당 주소의 빈 테스트 DB를 준비해야 하며 위 URI만 입력한다고 DB가 생성되지는 않는다. 환경 변수는 테스트에만 사용한다. 테스트는 루프백 주소와 테스트 DB 이름을 확인하고, 마이그레이션·seed·검증용 변경을 마지막에 롤백한다. 운영 DATABASE_URL을 사용하지 않는다.
