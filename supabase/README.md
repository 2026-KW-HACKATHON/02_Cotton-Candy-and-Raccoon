# 공지 DB — 수집 원문과 생성된 요약

팀이 실제 DB를 아직 생성·사용하지 않았다고 확인한 전제로, 공지 관련 변경을 기존 init에 통합했다. 별도 `notice_file_identity.sql`·`notice_source_identity.sql`은 init에 반영 후 제거했다. Holidays는 원본 그대로 유지했다. 통합 SQL은 develop에 반영됐으며 공식 DB에는 적용하지 않았다.

이 통합본은 **비어 있는 DB의 최초 생성용**이다. 이미 옛 마이그레이션을 적용한 DB를 업그레이드하는 파일이 아니며 init을 바꿔도 기존 DB는 바뀌지 않는다. 기존 데이터/마이그레이션 이력이 있는 환경에서는 그대로 적용하지 말고 팀과 별도 변경·초기화 절차를 정해야 한다. 이 작업은 사용자의 명시적인 초기 구조 통합 요청에 따른 협업 규칙의 예외이며, 향후 적용된 마이그레이션은 다시 수정하지 않는다.

## SQL 파일별 역할과 적용 순서

마이그레이션 9개를 파일명 순서대로 적용한다. 기존 DB에는 미적용 파일만 순서대로 적용하며, 이미 적용한 마이그레이션은 수정하지 않는다.

| 파일 | 역할 |
| --- | --- |
| [20260922053900_init.sql](migrations/20260922053900_init.sql) | 노원구·동·서울시 notices와 notice_files, 최종 식별 키·검증 제약·외래 키 생성 |
| [20260922053901_rls.sql](migrations/20260922053901_rls.sql) | 공개 공지·파일의 앱 조회 정책과 허용 파일 컬럼 설정 |
| [20260923044500_holidays.sql](migrations/20260923044500_holidays.sql) | 공휴일 판단·캐시용 holidays 테이블과 앱 접근 제한 설정 |
| [20261006120000_notice_summaries.sql](migrations/20261006120000_notice_summaries.sql) | notice_summaries, 내용 변경 시각, 요약의 제약·인덱스·읽기 권한 추가 |
| [20261007120000_notice_summaries_review_content.sql](migrations/20261007120000_notice_summaries_review_content.sql) | 검토 내용 보존·공개, 검토 마감 정렬 차단 |
| [20261007123000_notice_summary_card_summaries.sql](migrations/20261007123000_notice_summary_card_summaries.sql) | 조회용 generated 카드 컬럼·제약·읽기 권한 |
| [20261007130000_notice_summary_executions.sql](migrations/20261007130000_notice_summary_executions.sql) | 비공개 실행 토큰·백엔드 권한 |
| [20261007133000_notice_summary_source_revisions.sql](migrations/20261007133000_notice_summary_source_revisions.sql) | 원문 버전·변경 시 요약 무효화·오래된 결과 저장 차단 |
| [20261007140000_notice_summary_file_references.sql](migrations/20261007140000_notice_summary_file_references.sql) | 비공개 파일 연결 정보·조회용 generated 근거 링크·앱 권한 |
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

보정 재요청이 실패해도 형식 검사를 통과한 첫 요약은 보존한다. 기존 공개 결과가 있으면 그대로 유지하고 실패만 기록하며, 없으면 첫 결과를 `needs_review`, `deadline_on=NULL`로 저장한다. 실패 코드가 있는 행은 재시도 조회 대상이다.

`source_hash`는 본문 평문과 읽은 첨부 텍스트를 `file_key` 순으로 구성한 입력의 SHA-256 소문자 64자리다. PDF·이미지 바이트를 포함하는 확장은 후속 작업이다. 모델·프롬프트 버전은 안전한 식별자만, 오류는 코드 목록만 저장한다. `attempt_count`는 누적 실행 횟수이며 성공 후 초기화 정책은 추가하지 않는다.

마감일은 검증된 `dates` 중 `application`, `submission`, `payment`의 `end_date` 최댓값이다. 해당 날짜가 없으면 NULL이며, 검토 상태에서는 마감일 계산을 호출하지 않는다. Gemini가 마감일 DB 컬럼을 직접 정하지 않는다.

`notices.content_revision`은 본문·첨부 메타데이터가 바뀔 때 증가한다. 변경 시 기존 공개 결과·분류·마감일을 즉시 비우고 `summarized`를 `needs_review`로 바꾸며 성공 메타데이터와 시도 횟수는 유지한다. 원문이 같은 재수집은 버전을 올리지 않는다.

job 연동에서는 원문·파일과 같은 DB snapshot에서 읽은 `content_revision`을 `expected_source_revision`으로 **반드시 전달한다**. 비공개 실행 토큰과 원문 버전이 맞는 결과만 저장하며 오래된 실행은 `summary_execution_superseded`로 반환한다. 버전을 생략하는 호환 경로는 등록 전 원문 변경을 보호하지 못한다. API 호출 중 잠금을 유지하지 않으려면 autocommit 연결을 사용하며 commit은 호출자 책임이다.

앱 역할 `anon`, `authenticated`는 보이는 공지의 **10개 컬럼만** 읽는다: `notice_id`, `status`, `category`, `category_code`, `deadline_on`, `result`, `card_summaries`, `attachment_status`, `generated_at`, `file_references`. 내부 메타데이터·실행 레지스트리와 앱 쓰기는 차단하며 `service_role`에는 백엔드 조회·쓰기를 허용한다.

`file_manifest`는 준비기가 제공하는 원본 파일별 처리 결과와 전송 블록 연결 정보이며 비공개다. 저장 시 원문 버전·URL·전체 파일 ID/키/종류/URL을 대조한다. `file_references`는 파일 ID·종류·URL만 공개하는 generated 컬럼으로 직접 쓰지 않는다. 미등록 본문 이미지는 원문 공지 URL과 “원문에서 확인” 안내를 제공한다. 기존 요약을 유지하는 실패·보정 실패는 연결 정보도 유지하고, 원문 변경으로 `result`가 NULL이 되면 공개 링크도 NULL이 된다.

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

`supabase/tests/`의 `test_source_identity.py`, `test_notice_summary_schema.py`, `test_notice_summary_execution_schema.py`, `test_notice_summary_source_revision_schema.py`가 수집·요약·실행·원문 버전 계약을 검증한다. 마이그레이션 9개와 seed를 빈 임시 DB에 적용하며 검증 후 롤백한다. 파일 연결·실패 보존·실제 앱 권한·저장 경합은 파이프라인의 `test_summary_file_manifest_storage.py`에서 검사한다.

services/pipeline 폴더에서 PowerShell로 실행한다.

```powershell
$env:SCHEMA_TEST_DATABASE_URL = 'postgresql://pipeline_test@127.0.0.1:55442/pipeline_schema_test_init'
.\.venv\Scripts\python.exe -m pytest ../../supabase/tests -q -p no:cacheprovider
```

먼저 해당 주소의 빈 테스트 DB를 준비해야 하며 위 URI만 입력한다고 DB가 생성되지는 않는다. 환경 변수는 테스트에만 사용한다. 테스트는 루프백 주소와 테스트 DB 이름을 확인하고, 마이그레이션·seed·검증용 변경을 마지막에 롤백한다. 운영 DATABASE_URL을 사용하지 않는다.
