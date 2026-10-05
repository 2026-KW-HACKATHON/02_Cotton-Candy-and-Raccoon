-- ============================================================================
-- 앱의 읽기 권한
--
-- 앱은 보이는 공지와 그 파일의 일부 컬럼만 읽고, 쓰기는 전혀 할 수 없다.
-- pipeline은 DB 소유 계정(connection string)으로 직접 연결하므로
-- 아래 규칙의 영향을 받지 않는다.
--
-- Postgres의 접근 제어는 2층이고, 둘 다 통과해야 읽힌다.
--   1층 GRANT  - 테이블/컬럼 단위. 자격이 없으면 쿼리 자체가 거부된다.
--   2층 POLICY - 행 단위. 조건에 맞는 행만 남는다. 에러 없이 조용히 걸러진다.
--
-- 이 파일은 init migration 다음에 적용되어야 한다.
-- 통합 init의 nowon/dong/seoul에 동일한 공개 상태 규칙을 적용한다.
-- source_board는 공지 조회로 공개하지만 file_key는 파일 허용 컬럼에 넣지 않는다.
-- Supabase는 public 스키마의 새 테이블에 anon, authenticated의 select 권한을
-- 자동으로 준다. 이 파일을 빼먹으면 숨긴 공지와 file_name이 전부 공개된다.
-- ============================================================================


-- RLS를 켠다. 켜는 순간 policy에 걸리지 않은 행은 아무에게도 보이지 않는다
-- (테이블 소유자와 bypassrls 권한을 가진 역할은 예외이며, pipeline이 여기 해당한다).
alter table notices      enable row level security;
alter table notice_files enable row level security;


-- 2층: 보이는 공지만.
-- 앱이 select * from notices를 해도 Postgres가 where is_visible을
-- 끼워 넣은 것처럼 동작한다. 숨긴 글은 에러 없이 결과에서 빠진다.
--
-- anon        = 로그인 전 (publishable key만 가진 상태)
-- authenticated = 익명 로그인(Anonymous Sign-in)을 마친 상태
-- 앱은 기기 구분을 위해 익명 로그인을 쓰지만, 공지 읽기는 둘 다 허용한다.
create policy "read visible notices"
  on notices for select
  to anon, authenticated
  using (is_visible);


-- 2층: 보이는 공지에 딸린 파일만.
-- 숨긴 공지의 첨부파일 주소가 노출되지 않게 한다.
--
-- 이 subquery는 요청한 역할의 권한으로 실행되므로 notices의 policy도 함께 적용된다.
-- 결과적으로 같은 조건이 두 번 걸리는 셈이지만 판정은 일치한다.
create policy "read files of visible notices"
  on notice_files for select
  to anon, authenticated
  using (exists (
    select 1 from notices n
    where n.id = notice_id and n.is_visible
  ));


-- 1층: notice_files는 컬럼을 골라서만 읽게 한다.
--
-- file_name에 "직권조치결과공고문(이0진).pdf"처럼 마스킹된 성명이 들어갈 수 있다.
-- policy로는 행만 거를 수 있고 컬럼은 못 가리므로 GRANT 층에서 처리한다.
--
-- Supabase가 테이블 생성 시 자동으로 준 테이블 권한을 먼저 회수하고,
-- 필요한 컬럼만 다시 준다. 순서가 바뀌면 회수가 grant를 덮어쓴다.
--
-- 결과: 앱에서 select('*')는 permission denied로 실패한다.
--       select('id, notice_id, kind, url')처럼 컬럼을 명시해야 한다.
--
-- file_id와 file_sn을 뺀 것은 실질적 차단이 아니다. url에 q_fileId와 q_fileSn이
-- 그대로 들어 있기 때문이다. file_name과 file_key 등은 허용 목록 밖이다.
-- 기본 테이블 권한에 기대지 않고 읽기 전용 허용 목록을 명시한다.
-- 테이블 권한으로 쓰기·TRUNCATE도 막고 RLS로 공개 행만 남긴다.
revoke all on notices, notice_files from anon, authenticated;

grant  select on notices to anon, authenticated;
grant  select (id, notice_id, kind, url) on notice_files to anon, authenticated;


-- 쓰기 policy는 만들지 않는다.
-- RLS가 켜진 테이블에 insert/update/delete policy가 없으면 모두 거부된다.
-- 앱이 DB에 쓸 일이 생기면 그때 별도 테이블에 별도 policy로 연다.
