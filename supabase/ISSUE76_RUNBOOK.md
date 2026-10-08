# #76 운영 AI 스키마 적용

이 절차는 앱 view의 기존 `20261008150000` 이력이 있는 운영 DB에 누락된 AI 스키마를
적용한다. #26의 이전 요약 보존 정책은 변경하지 않으며 AI 활성화 설정도 바꾸지 않는다.

## 적용 범위

- `20261008160000`: 사전 공유 캐시
- `20261008170000`: 쉬운말 사전 후보
- `20261008190000`: 공지별 사전 연결과 조회 함수
- `20261008210000`: AI 처리 작업과 재시도 상태
- `20261008220000`: 기존 앱 view와 호환되는 공개 조회 계약

이미 공유된 SQL 내용은 변경하지 않는다. 다른 이력, 사전 캐시가 잘못된 번호로 적용된 DB,
SQL Editor의 부분 적용 상태는 자동 수정하지 않고 중단한다. 정상 적용을 마친 DB에서
재실행하면 신규 SQL을 적용하지 않고 데이터·권한만 검증한다.

## 실행 전

1. 작업 브랜치가 최신 develop의 통합 스키마를 포함하는지 확인한다.
2. `PIPELINE_DATABASE_URL`과 별도의 `PIPELINE_MIGRATION_BACKUP_KEY` secret을 준비한다.
   백업 키는 무작위 32바이트의 hex 문자열이다. 키는 로그·저장소·백업 artifact에 넣지 않는다.
   기존 artifact를 복호화할 수 있도록 키를 별도 보관하고 임의 교체하지 않는다.
3. 기존 수집 workflow의 수동 입력에 `operation=migrate-ai-db`, `mode=new`를 지정한다.
   이 모드에서는 수집·AI 단계가 실행되지 않는다. 기본 operation은 `collect`이므로
   기존 예약 실행과 수동 수집 동작은 유지된다.

## 백업과 적용 순서

1. 기존 public 테이블과 migration 이력을 잠그고 버전·스키마 표식을 대조한다.
2. 기존 테이블의 전체 컬럼·행과 anon/authenticated 앱 view 결과를 해시로 기록한다.
3. `pg_dump`로 public·supabase_migrations를 custom 형식으로 백업한다. 소유자는 제외하고
   권한·정책 정의는 보존한다. 전체 Supabase 서비스나 auth/storage 스키마의 백업은 아니다.
4. 백업과 검증 메타데이터를 AES-256-GCM으로 암호화한다. 인증된 암호문만 artifact에 올린다.
5. 별도 임시 PostgreSQL에서 백업을 복원하고 전체 기존 행 해시를 비교한다.
   복원 검사는 플랫폼 소유자·grant를 제외한다. 운영의 실제 권한은 적용 트랜잭션에서 따로 검증한다.
6. 암호화 백업을 GitHub artifact에 업로드한다. 업로드가 실패하면 적용하지 않는다.
7. 다시 DB를 잠그고 백업 후 데이터·앱 응답·버전 또는 로컬 SQL이 바뀌었으면 중단한다.
8. 미적용 SQL과 `supabase_migrations.schema_migrations` 이력을 같은 트랜잭션에서 반영한다.
9. 기존 행·공개 응답 보존, private 테이블의 앱 접근 차단, RLS, service_role 권한,
   사전 RPC 공개 실행 권한을 확인한 뒤 커밋한다. 결과 로그는 커밋 이후에만 출력한다.

백업·적용 중 테이블 쓰기 잠금을 사용한다. 이 workflow는 수집과 같은 concurrency 그룹을
사용하며, 다른 수동 작업과 충돌하면 lock timeout으로 중단한다. 원격 연결의 startup options에
의존하지 않고 트랜잭션에서 lock/statement timeout을 명시한다.

## 복구

- 커밋 전 SQL·검증 실패: 트랜잭션 전체가 롤백된다. 일부 DDL만 적용된 상태로 남기지 않는다.
- 백업 후 다른 수집이 실행된 경우: 오래된 백업으로 강행하지 말고 새 prepare부터 실행한다.
- 커밋 이후 문제: 해당 실행의 `issue76-backup-<run ID>` artifact와 별도 키를 보존한다.
  우선 격리된 DB에 복원해 상태를 비교하고 필요한 수정 migration을 준비한다.
  운영 DB를 자동 초기화하거나 백업으로 덮어쓰지 않는다.
- `restore-check`는 `127.0.0.1`의 `pipeline_schema_test_` 접두사 DB만 허용한다.
  이미 notices가 있는 대상은 거절한다. 운영 복원 명령으로 사용할 수 없다.

artifact 보존 기간은 30일이다. 그 이후에도 복구 자료가 필요하면 기간 만료 전에 암호화 파일과
해당 키를 조직의 백업 보관소로 옮긴다. 키 값을 GitHub 이슈·PR에 적지 않는다.

## 검증

- `supabase/tests/test_production_ai_migrations.py`: 정상·중복 실행, 데이터 drift, 번호 충돌,
  SQL 변경, view 누락, 이력 저장 실패, 권한 검증 실패의 롤백, 원격 복원 거절
- 실제 pg_dump → 암호화 → 복호화 → 임시 DB 복원 → 데이터 비교 후 적용 경로 검증
- 전체 DB 스키마·권한 및 기존 workflow 검사

적용 후 #63에서는 별도로 소량 AI 실행과 운영 DB 저장·앱 조회·예약 실행을 검증한다.
스키마 준비 완료만으로 #63 또는 AI 결과 품질 검증을 완료 처리하지 않는다.

## 운영 적용 기록 — 2026-10-09 KST

- 적용 코드: `8c792ca` (`feature/db/76-production-ai-migrations`).
- [운영 적용 실행](https://github.com/2026-KW-HACKATHON/02_Cotton-Candy-and-Raccoon/actions/runs/37804609151): 성공.
- 초기 4개와 `20261008150000` 이력을 확인한 뒤 위 5개 migration을 순서대로 적용했다.
  적용 이력은 총 10개이며 이력 번호를 강제로 조정하지 않았다.
- 기존 공지 43건, 첨부 80건 및 모든 기존 컬럼의 행 해시가 유지됐다.
  요약·쉬운말·요약 실행·공휴일 테이블은 각각 0건이었다.
- anon/authenticated 목록·상세 조회 결과 해시가 유지됐다. 내부 테이블 RLS와 접근 차단,
  사전 후보 컬럼 차단, 사전 RPC 실행 및 service_role 처리 권한을 확인한 후 커밋했다.
- 적용 전 백업을 임시 PostgreSQL 17에 복원해 기존 행 전체가 일치함을 확인했다.
  암호화 artifact 이름은 `issue76-backup-37804609151`이며 로컬에도 별도 보관했다.
  SHA-256: `48d5d449dd0d14dcd7fa45bc0f9f7847fdeaf57e0d68f34a43f1b21a4e2fc499`.
- DB 스키마·권한·workflow 관련 테스트 802개 통과, Ruff 및 diff 검사 통과.
- [커밋 후 별도 읽기 전용 확인](https://github.com/2026-KW-HACKATHON/02_Cotton-Candy-and-Raccoon/actions/runs/37805145140)도 성공했다.
  새 연결에서 적용 이력 10개, 필요한 테이블과 사전 후보 컬럼, 공지 및 앱 목록·상세 각각 43건을 확인했다.
  당시 처리 작업은 5건, 요약·쉬운말은 0건이었다. 이는 AI 처리 성공을 의미하지 않는다.
  DB 쓰기 없는 사전 API 조회는 `found`, 결과 4개였다.
- #26은 사용자 결정에 따라 작업 대상에서 제외했다. AI 활성화 설정은 변경하지 않았다.
  후속 작업은 #63의 소량 AI 처리·DB 저장·앱 조회·예약 실행 검증이다.
