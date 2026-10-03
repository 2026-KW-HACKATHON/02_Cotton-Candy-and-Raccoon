# 공지 DB — 이슈 #16 변경

기존 공유 마이그레이션 4개는 유지하고 `20261003090000_notice_source_identity.sql`을 추가했다. 운영 DB에는 적용하지 않았다. 종합 init으로 이력을 합치는 작업은 아직 하지 않았다. 향후 종합 init을 만들면 같은 변경을 중복 실행하지 않도록 기존 이력과 적용 방식을 함께 정리해야 한다.

## SQL 파일별 역할과 적용 순서

마이그레이션은 파일명 순서대로 적용한다. 뒤의 SQL이 앞의 제약을 변경하므로, 최초 init만 보면 현재 최종 구조와 다를 수 있다.

| 파일 | 역할 |
| --- | --- |
| [20260922053900_init.sql](migrations/20260922053900_init.sql) | notices·notice_files 테이블과 최초 제약·외래 키 생성 |
| [20260922053901_rls.sql](migrations/20260922053901_rls.sql) | 공개 공지·파일의 앱 조회 정책과 허용 파일 컬럼 설정 |
| [20260923044500_holidays.sql](migrations/20260923044500_holidays.sql) | 공휴일 판단·캐시용 holidays 테이블과 앱 접근 제한 설정 |
| [20260924222500_notice_file_identity.sql](migrations/20260924222500_notice_file_identity.sql) | 파일 고유 키를 (notice_id, file_id, kind)로 변경 |
| [20261003090000_notice_source_identity.sql](migrations/20261003090000_notice_source_identity.sql) | 서울시 분류·source_board 추가, 공지 고유 키 변경, 파일 식별자 NULL 허용 및 file_key 기반 고유 키로 변경 |
| [seed.sql](seed.sql) | 마이그레이션 적용 후 테스트용 공지·파일 데이터 입력. 스키마 변경 SQL이 아님 |

모든 마이그레이션을 적용한 최종 고유 키는 공지 `(category, source_board, post_sn)`, 파일 `(notice_id, file_key, kind)`다. 종합 init은 아직 별도 생성하지 않았다.

## 공지의 식별 기준

`notices` 고유 키는 `(category, source_board, post_sn)`이다. 세 값은 모두 문자열이다.

| 출처 | category | source_board | post_sn |
| --- | --- | --- | --- |
| 노원구 | nowon | 1001 | API ID |
| 월계1동 게시판 | dong | 1042 | q_bbscttSn |
| 서울시 | seoul | API BLOG_ID | API POST_ID |

서울시 BLOG_ID는 21·22·23·24·25·26·27·30을 허용한다. 분야가 다른 동일 POST_ID는 서로 다른 공지다. 월계1동 목록에 노출되는 다른 동 공지도 게시판 번호는 1042이고 `dong_group=other`로 분류한다. `source_board`는 동 분류가 아니다.

`nowon`과 `seoul`은 `dong_group=NULL`, `is_pinned=false`다. 기존 공지는 출처에 맞게 게시판 번호를 채우며 ID·본문·공개 상태는 유지한다.

## 파일의 식별 기준

`notice_files.notice_id`는 계속 `notices.id`를 참조한다. 새 고유 키는 `(notice_id, file_key, kind)`다.

- 실제 파일 ID가 있으면 `file_key = 'id:' + file_id`.
- ID가 없으면 `file_id=NULL`, `file_key = 'url:' + SHA256(저장할 정규화 URL의 UTF-8 바이트)`의 소문자 16진수 값.
- 실제 그룹 번호가 없으면 `file_sn=NULL`. 가짜 번호·UUID를 만들지 않는다.
- 같은 파일이 첨부이자 본문 이미지이면 kind가 다른 두 행을 허용한다.
- 같은 역할의 중복은 차단한다. file_sn은 중복 가능하다.

URL 해시는 파일 내용 해시가 아니다. URL이 바뀌면 다른 참조로 판단한다. URL 정규화는 저장 코드의 책임이며, SQL은 저장 URL과 file_key가 일치하는지만 검사한다. 기존 파일은 `id:<기존 file_id>`로 채워진다.

기존 앱 RLS·파일 컬럼 조회 권한은 바꾸지 않았다. 새 file_key는 앱 조회 허용 컬럼이 아니다.

## BE 적용 전 필수 작업

새 컬럼에 기본값이 없으므로 **기존 BE 저장 코드를 그대로 쓰면 실패한다.** DB와 저장 계약을 함께 배포해야 한다.

1. 공지 모델·변환·INSERT·upsert·중복 판단에 source_board를 반영한다.
2. 파일 모델에서 file_sn/file_id를 선택값으로 바꾸고 file_key를 계산·저장한다.
3. 파일 집합 비교와 중복 제거를 file_key + kind 기준으로 맞춘다.
4. Gemini 가공 브랜치의 DB 조회 모델을 nullable 식별자에 맞추고 서울시 다운로드 호스트를 지원한다.

Gemini에 전달하는 텍스트·PDF·이미지 입력 형식은 이 변경만으로 바뀌지 않는다. notice_id로 파일을 조회하는 관계도 동일하다. 실제 Gemini 전송과 서울시 수집은 이번 변경에서 구현하지 않았다.

## DB 검증

`supabase/tests/test_source_identity.py`는 별도의 비어 있는 임시 PostgreSQL을 사용한다. 실제 Supabase Data API 검증은 아니다. 역할 생성 권한이 필요하며 테스트용 새 클러스터를 사용한다.

services/pipeline 폴더에서 PowerShell로 실행한다.

```powershell
$env:SCHEMA_TEST_DATABASE_URL = 'postgresql://pipeline_test@127.0.0.1:55439/pipeline_schema_test_16'
.\.venv\Scripts\python.exe -m pytest ../../supabase/tests/test_source_identity.py -q -p no:cacheprovider
```

환경 변수는 테스트에만 사용한다. 테스트는 루프백 주소와 테스트 DB 이름을 확인하고, 마이그레이션·seed·검증용 변경을 마지막에 롤백한다. 운영 DATABASE_URL을 사용하지 않는다.
