# 공지 DB — 수집 원문과 생성된 요약

팀이 실제 DB를 아직 생성·사용하지 않았다고 확인한 전제로, 공지 관련 변경을 기존 init에 통합했다. 별도 `notice_file_identity.sql`·`notice_source_identity.sql`은 init에 반영 후 제거했다. Holidays는 원본 그대로 유지했다. 통합 SQL은 develop에 반영됐으며 공식 DB에는 적용하지 않았다.

이 통합본은 **비어 있는 DB의 최초 생성용**이다. 이미 옛 마이그레이션을 적용한 DB를 업그레이드하는 파일이 아니며 init을 바꿔도 기존 DB는 바뀌지 않는다. 기존 데이터/마이그레이션 이력이 있는 환경에서는 그대로 적용하지 말고 팀과 별도 변경·초기화 절차를 정해야 한다. 이 작업은 사용자의 명시적인 초기 구조 통합 요청에 따른 협업 규칙의 예외이며, 향후 적용된 마이그레이션은 다시 수정하지 않는다.

## SQL 파일별 역할과 적용 순서

마이그레이션은 아래 7개를 파일명 순서대로 적용한다. 수집 원문·파일 제약은 init에서 생성하며, #14의 요약과 내용 변경 시각을 추가한 뒤 후속 마이그레이션으로 검토 결과 공개 계약, 조회용 카드 컬럼과 비공개 실행 순서 레지스트리를 추가한다. 이미 요약 테이블을 생성한 DB에는 아직 적용하지 않은 후속 마이그레이션을 순서대로 적용한다. 기존 마이그레이션은 수정하지 않는다.

| 파일 | 역할 |
| --- | --- |
| [20260922053900_init.sql](migrations/20260922053900_init.sql) | 노원구·동·서울시 notices와 notice_files, 최종 식별 키·검증 제약·외래 키 생성 |
| [20260922053901_rls.sql](migrations/20260922053901_rls.sql) | 공개 공지·파일의 앱 조회 정책과 허용 파일 컬럼 설정 |
| [20260923044500_holidays.sql](migrations/20260923044500_holidays.sql) | 공휴일 판단·캐시용 holidays 테이블과 앱 접근 제한 설정 |
| [20261006120000_notice_summaries.sql](migrations/20261006120000_notice_summaries.sql) | notice_summaries, 내용 변경 시각, 요약의 제약·인덱스·읽기 권한 추가 |
| [20261007120000_notice_summaries_review_content.sql](migrations/20261007120000_notice_summaries_review_content.sql) | 검토 결과 JSON·분류 공개 허용, 미확인 마감 정렬 차단, 기존 NULL 검토 행 호환 |
| [20261007123000_notice_summary_card_summaries.sql](migrations/20261007123000_notice_summary_card_summaries.sql) | 원본 result의 카드 JSON에서 조회용 generated 컬럼 생성, 네 키·문자열 제약과 앱 읽기 권한 추가 |
| [20261007130000_notice_summary_executions.sql](migrations/20261007130000_notice_summary_executions.sql) | 앱 비공개 최신 실행 토큰·sequence와 백엔드 전용 RLS·권한 추가, 공지 삭제 시 cascade |
| [20261007133000_notice_summary_source_revisions.sql](migrations/20261007133000_notice_summary_source_revisions.sql) | 원문 버전 카운터·본문/파일 변경 시 기존 공개 요약 무효화·실행의 원문 버전 보호 추가 |
| [seed.sql](seed.sql) | 테스트용 공지·파일, 요약 상태 4종·부분 읽기·숨김 공지 데이터. 스키마 변경 SQL이 아님 |

init부터 공지 고유 키는 `(category, source_board, post_sn)`, 파일 고유 키는 `(notice_id, file_key, kind)`다. 별도의 새 init 파일을 추가한 것이 아니라 기존 init에 공지 변경을 합쳤다.

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

앱의 조회 범위는 기존과 같다. nowon/dong/seoul의 공개 공지는 조회할 수 있고, 파일은 `id, notice_id, kind, url`만 허용한다. `file_name, file_sn, file_id, file_key`는 허용 컬럼이 아니다. RLS에서 숨김 공지와 그 파일을 제외한다. 기본 테이블 권한을 회수하고 읽기 권한만 명시하여 INSERT/UPDATE/DELETE/TRUNCATE를 허용하지 않는다. Holidays 접근 제한은 기존 파일 그대로다.

## 요약 저장 계약 — 이슈 #14

`notice_summaries`는 `notice_id`를 PK·FK로 사용해 공지 한 건당 한 행을 저장한다. 공지를 삭제하면 요약도 함께 삭제된다. `category`는 공지 유형(`application`, `event`, `living`, `obligation`, `news`, `mixed`)이며, 수집 출처를 뜻하는 `notices.category`와 구분한다. `needs_review`도 생성된 유형을 보존한다. 유형이 `unknown`이면 JSON에는 그대로 보존하고 DB `category`는 NULL로 저장한다.

분야는 별도 정수 `category_code`로 저장한다. `21=교통`, `22=안전`, `23=주택`, `24=경제`, `25=환경`, `26=문화`, `27=복지`, `30=행정`만 허용한다. 서울시 게시판의 `source_board`는 수집 식별용 문자열이며 Gemini가 내용으로 분류하는 `category_code`와 별개다. `summarized`의 JSON은 객체여야 하고, JSON의 공지 유형·정수 분야 코드가 두 DB 컬럼과 각각 일치해야 한다. JSON 문자열 `"27"`, 소수 `27.0`, 허용 목록 밖 코드, 누락·NULL은 거부한다. `needs_review`의 JSON도 객체이고 두 분류 필드를 명시해야 한다. 알려진 분류는 DB 컬럼과 일치시키며, `category="unknown"`은 DB NULL, JSON `category_code=null`은 DB NULL을 허용한다. 검토 상태에서도 누락 필드·잘못된 JSON 타입·분류 불일치는 거부한다. 세부 요약 스키마와 원문 근거 검증은 파이프라인에서 수행한다.

| status | 공개 결과 |
| --- | --- |
| `pending` | 공개 요약이 없는 대기 상태 |
| `summarized` | 검증을 통과한 전체 `NoticeSummary`와 evidence |
| `needs_review` | 생성된 `NoticeSummary`·유형·분야를 앱에 제공하고 네 카드에 **“(원문 확인 요함)”** 안내 |
| `failed` | 기존 저장 행이 없던 최초 실행 실패 |

`pending`, `failed`의 `result`, `category`, `category_code`, `deadline_on`은 모두 NULL이다. 파일 참조만 확인했거나 일부 첨부를 읽지 못해 `needs_review`가 되어도 생성된 요약과 네 카드·분류는 제공하고 원문 확인 안내를 표시한다. 검토 상태의 `deadline_on`은 항상 NULL로 두어 미확인 마감일이 정렬에 사용되지 않도록 한다. 보존할 생성 결과가 없는 기존 검토 행이나 원문 변경 후 재요약 실패 행은 `result`, `category`, `category_code`, `deadline_on`이 모두 NULL인 형태를 계속 허용한다.

새 Gemini 응답에는 기존 `NoticeSummary` 필드를 모두 유지하면서 `card_summaries` 객체를 추가한다. 키는 `audience`, `deadline`, `action`, `notes` 네 개로 고정하고, 각각 비어 있지 않은 한 줄 문자열 또는 JSON `null`을 담는다. 추가 키·빠진 키·문자열 외 값·공백만 있는 문자열·CR/LF 줄바꿈은 DB CHECK에서도 거부한다. 문자열은 표시 길이로 자르거나 변환하지 않고 원본 `result.card_summaries` 안에 보존한다. 새 응답에는 이 객체가 필수지만, 기존 저장 JSON의 누락·명시적 `null`과 결과 없는 행은 계속 허용한다.

조회 편의용 `notice_summaries.card_summaries`는 `GENERATED ALWAYS AS (NULLIF(result -> 'card_summaries', 'null'::jsonb)) STORED` 컬럼이다. 백엔드는 전체 `result`만 기존 INSERT/upsert로 저장하며 별도로 카드 컬럼을 쓰지 않는다. 카드 컬럼은 원본과 항상 일치하고, 레거시 누락·JSON `null`·`result=NULL`은 SQL NULL로 조회된다. 동일 입력의 재요약 실패로 `result`를 유지하면 카드도 유지되며, 원문 변경 후 실패로 `result`를 비우면 카드 컬럼도 자동으로 NULL이 된다. 카드 문장의 원문 근거 검증과 `needs_review`의 원문 확인 경고는 파이프라인의 공개 뷰 계약을 따른다.

기존 필드를 유지하는 핵심 목적은 앱의 **“원문 근거 보기”**다. `card_summaries`와 공개 카드의 `text`는 읽기 편한 카드 문구이며, 기존 필드값·`items`·`metadata`·`source_path`·`evidence`와 파일의 `source_id`, `page`, `verification`은 근거 확인에 사용한다. AI가 추출한 필드값을 원문 직접 인용처럼 표시하지 않고 실제 원문 발췌인 `evidence.excerpt`와 구분한다. 파일 참조만 확인한 경우 내용을 검증했다고 표시하지 않는다. 새 카드 문구에 독립된 근거가 추가된 것은 아니며 네 문구 전체의 의미가 완전히 검증되었다고 약속하지 않는다. 기존 근거는 필드 단위로 연결되므로 배열의 개별 항목까지 검증한 것으로 표시하지 않는다.

동일한 입력으로 다시 실행하다 실패하면 기존 상태·결과·분류·기한·첨부 상태·입력 해시·모델·프롬프트 버전·생성 시각을 유지하고 `last_error_code`, `attempt_count`, `updated_at`만 갱신한다. #24의 변경된 원문에 대한 재요약 실패는 기존 `summarized`를 `needs_review`로 바꾸고 공개 4컬럼을 비운다. 이전 성공의 `source_hash`와 생성 메타데이터는 유지해 새 입력을 성공 처리한 것으로 표시하지 않는다. 이 경우와 기존 요약의 실패 기록도 재시도 대상으로 조회하도록 `notice_summaries_retry_idx`는 `pending/failed` 또는 `last_error_code IS NOT NULL` 행을 포함한다.

`source_hash`는 본문 평문과 읽은 첨부 텍스트를 `file_key` 순으로 구성한 입력의 SHA-256 소문자 64자리다. PDF·이미지 바이트를 포함하는 확장은 후속 작업이다. 모델·프롬프트 버전은 안전한 식별자만, 오류는 코드 목록만 저장한다. `attempt_count`는 기록된 실행의 누적 횟수이며 성공 후 초기화 정책은 추가하지 않는다. 기본 job의 종료 저장만 사용할 때 `superseded` 종료는 요약 행과 카운터를 갱신하지 않는다. 대체된 실행까지 포함한 모든 시작을 세려면 외부 `pending=1`, public job 완료 `attempt_increment=0` 계약을 사용한다.

마감일은 검증된 `dates` 중 `application`, `submission`, `payment`의 `end_date` 최댓값이다. 해당 날짜가 없으면 NULL이며, 검토 상태에서는 마감일 계산을 호출하지 않는다. Gemini가 마감일 DB 컬럼을 직접 정하지 않는다.

비공개 `notice_summary_executions`는 공지별 최신 시작 토큰과 등록 당시 `source_revision`을 저장한다. `begin_summary_execution()`으로 Gemini 호출 전에 등록하고 그 토큰을 종료 저장에 전달하면, 공지 원문 행과 레지스트리를 같은 순서로 잠근 뒤 최신 토큰·원문 버전이 모두 일치하는 결과·실패만 적용한다. 늦거나 원문이 바뀐 실행은 `summary_execution_superseded`로 구분하며 현재 결과·카드·마감일·실패 코드를 덮거나 새 행을 삽입하지 않는다. 기존 토큰 없는 저수준 저장 호출은 호환을 유지하지만 실행 순서·원문 버전 보호를 제공하지 않는다. 앱은 이 테이블과 sequence에 접근할 수 없으며 `service_role`만 등록·조회·쓰기를 할 수 있다.

등록도 호출자 트랜잭션을 사용한다. API 호출 사이 DB 트랜잭션을 열어 두지 않으려면 autocommit 연결을 사용하거나 저수준 등록을 명시적으로 commit한 뒤 호출한다. 기본 트랜잭션에서는 commit/rollback까지 같은 공지의 원문 SHARE·레지스트리 잠금이 유지되어 원문 수정과 다른 실행 등록이 기다린다. 파이프라인이 호출자 데이터를 임의로 commit하지 않는다. 원문·파일을 읽은 동일한 DB snapshot의 `notices.content_revision`을 `begin_summary_execution()` 또는 public job의 `expected_source_revision`으로 전달하면 등록 전에 이미 바뀐 입력을 API 호출 없이 거부한다. 이 값을 생략하는 호환 경로는 등록 이후 변경만 보호하며 이미 오래된 prepared 입력을 식별하지 못한다. #13 준비/조회 연동에서는 이 버전을 반드시 함께 전달해야 한다.

`notices.content_revision`은 1부터 시작한다. 본문·제목·담당 부서·게시일·URL·공공누리·출처 식별 정보 변경과 `notice_files`의 실제 INSERT/UPDATE/DELETE 때 DB trigger가 증가시킨다. 같은 트랜잭션에서 `content_updated_at`이 같아도 서로 다른 버전을 구별한다. 변경되지 않은 원문·파일과 표시 여부·단순 수집 시각 갱신은 버전을 올리지 않는다. 원문 변경 시 기존 공개 `result`·유형·분야·마감일을 즉시 비우고 `summarized`를 `needs_review`로 바꾼다. 성공 입력·생성 메타데이터와 시도 횟수는 유지하며 재요약 실패로 집계하지 않는다. 수집 트랜잭션이 롤백되면 버전과 요약 무효화도 함께 롤백된다. 파일 URL·참조 메타데이터가 그대로인 상태에서 원격 파일의 바이트만 바뀐 경우는 여전히 감지하지 못한다.

앱 역할 `anon`, `authenticated`는 보이는 공지의 다음 **9개 컬럼만** 읽는다: `notice_id`, `status`, `category`, `category_code`, `deadline_on`, `result`, `card_summaries`, `attachment_status`, `generated_at`. 내부 메타데이터와 `updated_at`은 차단되므로 `select('*')`도 실패한다. 앱의 쓰기 권한·쓰기 policy는 없다. 기존 RLS는 그대로 유지하며 새 카드 컬럼에도 SELECT만 추가한다. `service_role`에는 백엔드 조회·쓰기 권한과 전용 policy를 명시하지만 generated 카드 컬럼은 직접 쓸 수 없다.

`notices.content_updated_at`은 기존 공지에는 `created_at`으로 채우고 신규 공지에는 현재 시각을 넣는다. 이후 파이프라인이 실제 본문·제목 등의 내용 또는 파일 목록 변경 시에만 갱신한다. 단순 재수집 시간인 `updated_at`, 수정 이력 표시인 `is_modified`와 역할이 다르다. 앱은 기존 공지 읽기 권한으로 이 새 컬럼을 조회한다.

## BE 호환과 배포 주의사항

새 컬럼에 기본값이 없으므로 **변경 전 BE 저장 코드를 그대로 쓰면 실패한다.** #18 브랜치에서는 아래 1~3을 반영하고 임시 PostgreSQL로 저장을 검증했다. DB와 새 저장 코드를 함께 배포해야 하며, 공식 DB에는 아직 적용하지 않았다.

1. 공지 모델·변환·INSERT·upsert·중복 판단에 source_board를 반영한다.
2. 파일 모델에서 file_sn/file_id를 선택값으로 바꾸고 file_key를 계산·저장한다.
3. 파일 집합 비교와 중복 제거를 file_key + kind 기준으로 맞춘다.
4. Gemini 가공 브랜치의 DB 조회 모델을 nullable 식별자에 맞추고 서울시 다운로드 호스트를 지원한다.

4번 Gemini 가공 연동은 별도 #13 작업이다. notice_id로 파일을 조회하는 관계는 유지된다. #18은 1~3번 저장 계약 연동과 서울시 한 건·분야별 최초 25건·평소 10건 CLI를 구현했다. 현재 서울시는 원문 크롤링 없이 API POST_CONTENT와 그 안의 파일 URL만 저장하며, 본문 밖 별도 첨부는 수집하지 않는다. API에 공지별 공공누리가 없어 license_type은 NULL이다. 예전 발급 키 검증의 공지195·파일430행 및 원문 리다이렉트5건은 정책 변경 전 이력이며 현재 API 전용 검증이 아니다. Actions 예약 연결·실제 Gemini 전송·실제 Supabase 앱 키 조회는 별도 작업이다. 통합 init의 최종 DB 저장 계약은 변경하지 않았으며 공식 DB에는 쓰지 않았다.

## DB 검증

2026-10-06 임시 PostgreSQL **17.11**에서 마이그레이션 4개와 seed를 적용하는 스키마 테스트가 **153 passed, 0 skipped**로 통과했다. 이전 공지의 내용 변경 시각 backfill, 요약 상태·JSON 분류 제약, 앱의 실제 역할별 SQL 조회·쓰기 차단, 서비스 역할 권한을 검증했다. Ruff와 diff 검사도 통과했다. 테스트 변경은 롤백했으며 실제 Supabase 프로젝트나 Data API에는 접근하지 않았다. `npx supabase db reset` 성공을 뜻하는 검증은 아니다.

2026-10-04 임시 PostgreSQL17.11에서 **39 passed, 0 skipped**, Ruff와 diff 검사 통과. 기존5개 SQL과 통합3개 SQL의 컬럼28개·제약15개·인덱스·RLS정책2개·RLS활성 상태를 비교해 동일함을 확인했다(컬럼의 물리적 순서는 비교하지 않음). 앱의 테이블 쓰기 권한은 명시적으로 회수했다. Holidays·seed는 diff가 없다. 검증용 임시 서버·DB·로그는 종료/삭제했으며 실제 Supabase 프로젝트 키 조회는 수행하지 않았다.

`supabase/tests/test_source_identity.py`는 수집 스키마를, `test_notice_summary_schema.py`는 실제 요약 테이블과 RLS·컬럼 권한을 검증한다. 공통 fixture가 마이그레이션 8개와 seed를 비어 있는 임시 PostgreSQL에 적용하고, 기존 공지의 변경 시각·카드 generated 컬럼 backfill과 이미 등록된 실행의 버전 backfill·토큰 갱신도 확인한다. `test_notice_summary_execution_schema.py`는 비공개 레지스트리·sequence의 앱 접근 차단, backend 등록·RLS·cascade를 확인한다. `test_notice_summary_source_revision_schema.py`는 버전 제약·trigger 활성화·앱의 버전 쓰기 차단·service 전용 정책을 확인한다. 별도 pipeline `test_threepass_database_audit.py`는 두 연결의 경쟁·원문 변경 직후 공개 요약 무효화·파일 변경·롤백·등록 전에 오래된 입력·실제 job/SQL/앱 역할 공개 뷰를 검증한다. 기존 seed의 카드 없는 요약과 NULL 검토 행은 레거시 호환 사례다. 실제 Supabase Data API 검증은 아니다. 테스트 역할이 이미 있으면 높은 권한과 상속이 없는 NOLOGIN 역할인지 검사한 뒤 재사용하며, 검증 변경은 전용 빈 DB에서 롤백한다.

services/pipeline 폴더에서 PowerShell로 실행한다.

```powershell
$env:SCHEMA_TEST_DATABASE_URL = 'postgresql://pipeline_test@127.0.0.1:55442/pipeline_schema_test_init'
.\.venv\Scripts\python.exe -m pytest ../../supabase/tests -q -p no:cacheprovider
```

먼저 해당 주소의 빈 테스트 DB를 준비해야 하며 위 URI만 입력한다고 DB가 생성되지는 않는다. 환경 변수는 테스트에만 사용한다. 테스트는 루프백 주소와 테스트 DB 이름을 확인하고, 마이그레이션·seed·검증용 변경을 마지막에 롤백한다. 운영 DATABASE_URL을 사용하지 않는다.
