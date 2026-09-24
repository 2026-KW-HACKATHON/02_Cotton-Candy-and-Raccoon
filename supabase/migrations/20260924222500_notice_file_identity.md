# 공지 파일 식별 키 변경 기록

관련 이슈: #7 `[DB] 공지 첨부파일 고유 키에 file_id 포함`

## 추가된 파일

- `20260924222500_notice_file_identity.sql`: `notice_files`의 고유 제약을 `(notice_id, file_sn)`에서 `(notice_id, file_sn, file_id)`로 교체한다. 같은 공지에서 `file_sn`이 같고 `file_id`가 다른 파일은 각각 저장할 수 있고, 세 값이 모두 같은 파일은 중복 저장할 수 없다.
- `20260924222500_notice_file_identity.md`: 변경 이유와 BE 연동 사항 및 검증 상태를 기록한다.

## 수정된 파일

- 없음. `20260922053900_init.sql`은 최초 스키마 기록이므로 그대로 둔다. 새 DB에는 초기 마이그레이션 후 이번 마이그레이션이 순서대로 적용된다. 기존 DB에는 이번 마이그레이션만 추가 적용된다.

## 영향 및 후속 작업

- 컬럼, 기존 파일 데이터, 외래 키, RLS·읽기 권한은 변경하지 않는다.
- BE 브랜치 `feature/be/4-collect-notices`의 첨부 중복 제거는 현재 `file_sn`만 기준으로 한다. DB 변경이 통합되면 `(file_sn, file_id)` 기준으로 맞추고, 같은 `file_sn`에 다른 `file_id`가 있는 경우의 오류 처리와 테스트를 수정해야 한다.
- 실제 Supabase DB에 이 마이그레이션을 적용하거나 운영 데이터를 변경하지 않았다.

## 검증

- SQL 정적 점검: 기존 제약 이름과 `notice_files` 컬럼을 초기 마이그레이션에서 확인했다.
- DB 실행 검증: 로컬에 Supabase CLI, `psql`, Docker가 없어 아직 수행하지 못했다. 적용 가능한 환경에서 동일한 `notice_id`와 `file_sn`에 다른 `file_id` 두 개가 저장되는지, 세 값이 모두 같은 중복이 거부되는지 확인해야 한다.
