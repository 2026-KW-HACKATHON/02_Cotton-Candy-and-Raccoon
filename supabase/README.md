# 공지 DB — 이슈 #16 변경

팀이 실제 DB를 아직 생성·사용하지 않았다고 확인한 전제로, 공지 관련 변경을 기존 init에 통합했다. 별도 `notice_file_identity.sql`·`notice_source_identity.sql`은 init에 반영 후 제거했다. Holidays는 원본 그대로 유지했다. 공식 DB에 적용하거나 Git 작업을 실행하지는 않았다.

이 통합본은 **비어 있는 DB의 최초 생성용**이다. 이미 옛 마이그레이션을 적용한 DB를 업그레이드하는 파일이 아니며 init을 바꿔도 기존 DB는 바뀌지 않는다. 기존 데이터/마이그레이션 이력이 있는 환경에서는 그대로 적용하지 말고 팀과 별도 변경·초기화 절차를 정해야 한다. 이 작업은 사용자의 명시적인 초기 구조 통합 요청에 따른 협업 규칙의 예외이며, 향후 적용된 마이그레이션은 다시 수정하지 않는다.

## SQL 파일별 역할과 적용 순서

마이그레이션은 아래 3개를 파일명 순서대로 적용한다. 공지 관련 최종 컬럼·제약은 init에서 바로 생성하며 RLS는 별도 파일에서 적용한다.

| 파일 | 역할 |
| --- | --- |
| [20260922053900_init.sql](migrations/20260922053900_init.sql) | 노원구·동·서울시 notices와 notice_files, 최종 식별 키·검증 제약·외래 키 생성 |
| [20260922053901_rls.sql](migrations/20260922053901_rls.sql) | 공개 공지·파일의 앱 조회 정책과 허용 파일 컬럼 설정 |
| [20260923044500_holidays.sql](migrations/20260923044500_holidays.sql) | 공휴일 판단·캐시용 holidays 테이블과 앱 접근 제한 설정 |
| [seed.sql](seed.sql) | 마이그레이션 적용 후 테스트용 공지·파일 데이터 입력. 스키마 변경 SQL이 아님 |

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

## BE 적용 전 필수 작업

새 컬럼에 기본값이 없으므로 **기존 BE 저장 코드를 그대로 쓰면 실패한다.** DB와 저장 계약을 함께 배포해야 한다.

1. 공지 모델·변환·INSERT·upsert·중복 판단에 source_board를 반영한다.
2. 파일 모델에서 file_sn/file_id를 선택값으로 바꾸고 file_key를 계산·저장한다.
3. 파일 집합 비교와 중복 제거를 file_key + kind 기준으로 맞춘다.
4. Gemini 가공 브랜치의 DB 조회 모델을 nullable 식별자에 맞추고 서울시 다운로드 호스트를 지원한다.

Gemini에 전달하는 텍스트·PDF·이미지 입력 형식은 이 변경만으로 바뀌지 않는다. notice_id로 파일을 조회하는 관계도 동일하다. 실제 Gemini 전송과 서울시 수집은 이번 변경에서 구현하지 않았다.

## DB 검증

2026-10-04 임시 PostgreSQL17.11에서 **39 passed, 0 skipped**, Ruff와 diff 검사 통과. 기존5개 SQL과 통합3개 SQL의 컬럼28개·제약15개·인덱스·RLS정책2개·RLS활성 상태를 비교해 동일함을 확인했다(컬럼의 물리적 순서는 비교하지 않음). 앱의 테이블 쓰기 권한은 명시적으로 회수했다. Holidays·seed는 diff가 없다. 검증용 임시 서버·DB·로그는 종료/삭제했으며 실제 Supabase 프로젝트 키 조회는 수행하지 않았다.

`supabase/tests/test_source_identity.py`는 통합 init·RLS·Holidays와 seed를 비어 있는 임시 PostgreSQL에서 검증하는 개발용 테스트다. SQL 실행 목록이나 배포 데이터가 아니며 운영 실행에 필요하지 않다. 서울시 분야별 식별, 앞자리0, 파일 중복/두 역할, NULL 식별자, 공개/숨김 조회, 비공개 컬럼·앱 쓰기 차단을 확인한다. 실제 Supabase Data API 검증은 아니다. 역할 생성 권한이 필요하며 테스트용 새 클러스터를 사용한다.

services/pipeline 폴더에서 PowerShell로 실행한다.

```powershell
$env:SCHEMA_TEST_DATABASE_URL = 'postgresql://pipeline_test@127.0.0.1:55442/pipeline_schema_test_init'
.\.venv\Scripts\python.exe -m pytest ../../supabase/tests/test_source_identity.py -q -p no:cacheprovider
```

먼저 해당 주소의 빈 테스트 DB를 준비해야 하며 위 URI만 입력한다고 DB가 생성되지는 않는다. 환경 변수는 테스트에만 사용한다. 테스트는 루프백 주소와 테스트 DB 이름을 확인하고, 마이그레이션·seed·검증용 변경을 마지막에 롤백한다. 운영 DATABASE_URL을 사용하지 않는다.
