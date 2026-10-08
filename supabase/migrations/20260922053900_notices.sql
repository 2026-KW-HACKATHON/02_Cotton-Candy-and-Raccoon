-- ============================================================================
-- notices, notice_files: 수집 원문과 파일 참조
--
-- 최초 생성용 스키마 4개 중 첫 번째다. 2026-10-08까지의 migration 14개를
-- 기능별 최종 상태로 재구성했다. 원격 Supabase DB를 만들기 전에 재구성했으므로
-- 기존 DB 업그레이드용이 아니다. 이후 변경은 새 migration 파일로 추가한다.
--
--   20260922053900_notices.sql           notices, notice_files, 앱 읽기 권한
--   20260922053901_holidays.sql          holidays
--   20260922053902_notice_summaries.sql  요약, 요약 실행, 원문 변경 trigger
--   20260922053903_notice_easy_texts.sql 쉬운말
--
-- 쓰기: pipeline(Python)이 DB 소유 계정 또는 service_role로 직접 연결한다.
-- 읽기: 앱(Expo)이 publishable key(anon)로 읽는다. 공개 범위는 아래 권한 절에서 정한다.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- notices: 공지 한 건이 한 행
-- ----------------------------------------------------------------------------
create table notices (
  -- DB 내부 번호. 공지 자체의 번호가 아니라 이 테이블의 식별자다.
  -- notice_files가 이 값을 참조한다.
  id             bigint      generated always as identity primary key,

  -- 출처: nowon = 노원구, dong = 동 게시판, seoul = 서울시.
  category       text        not null,

  -- 게시판/분야 식별자. 노원구 1001, 동 게시판 1042, 서울시 API BLOG_ID.
  -- 같은 동 게시판에 표시되는 다른 동 고정 글도 1042다. dong_group과 다르다.
  source_board   text        not null,

  -- 동 공지가 어느 동 것인지. 'wolgye1' = 월계1동, 'other' = 다른 동.
  -- 노원구·서울시 공지에는 해당이 없어 null.
  -- 판단 기준: 번호 붙은 행은 월계1동 목록에서 왔으므로 wolgye1,
  --            고정 행은 부서 표기가 "월계1동"으로 시작하면 wolgye1, 아니면 other.
  dong_group     text,

  -- 목록 상단 "공지" 행으로 올라와 있는지.
  -- 다른 동 글은 고정이 풀리면 볼 이유가 없어져 is_visible도 false가 된다.
  is_pinned      boolean     not null default false,

  -- 게시물 번호. 노원 API ID, 동 게시판 q_bbscttSn, 서울시 API POST_ID.
  -- 앞자리 0이 의미를 가질 수 있어 숫자형이 아니라 text로 둔다.
  post_sn        text        not null,

  title          text        not null,

  -- 부서 표기를 받은 그대로 저장한다. dong_group 판단에 쓰인다.
  -- 노원 API의 MANAGER는 sample 5건 모두 DEPARTMENT와 같아서 저장하지 않는다.
  department     text,

  -- 등록일 (KST). 서울시의 시각 포함 값도 저장 시 날짜로 변환한다.
  -- 이 때문에 "받은 범위의 가장 오래된 날짜(D)" 당일 글은 범위 안인지 밖인지
  -- 판단할 수 없고, 동기화 규칙에서 D 당일 글은 숨김 대상에서 제외한다.
  registered_on  date        not null,

  -- 원문 링크. http://www.nowon.kr:80/ 형태를 https://www.nowon.kr/로 통일해 저장한다.
  url            text        not null,

  -- 본문 HTML을 받은 그대로. 태그와 공백을 지웠을 때 비면 null.
  -- 노원 API의 DESCRIPTION은 XML 파싱 결과가 이미 HTML이므로
  -- html.unescape를 한 번 더 하면 본문 속 &lt;가 태그로 바뀌어 깨진다.
  body_html      text,

  -- 공공누리 유형. 노원 API는 데이터셋 제4유형이라 KOGL-4,
  -- 동 게시판·서울시는 원문 표시값을 쓴다. 표시가 없으면 null.
  license_type   text,

  -- 한 번이라도 수정된 적이 있는지. 앱에서 "수정됨" 표시에 쓴다.
  -- 한 번 true가 되면 되돌리지 않는다. 이전 내용은 남기지 않는다.
  is_modified    boolean     not null default false,

  -- 앱에 보일지. 삭제되거나 고정이 풀린 글을 지우는 대신 false로 둔다.
  -- 지우지 않는 이유는 같은 글이 다시 나타났을 때 재사용하고,
  -- 한 번 수집한 기록을 잃지 않기 위해서다.
  is_visible     boolean     not null default true,

  -- 행이 처음 들어온 시각. 수집 시점 추적용.
  created_at     timestamptz not null default now(),

  -- pipeline이 덮어쓸 때 now()로 직접 갱신한다. 이 컬럼을 갱신하는 trigger는 없다.
  -- 동기화가 조용히 멈췄을 때 이 값으로 알아차린다.
  updated_at     timestamptz not null default now(),

  -- 제목, 본문, 파일 목록 등 원문이 실제로 바뀐 시각. 내용이 같은 재수집은 바꾸지 않는다.
  -- 원문 변경 trigger(notice_summaries migration)가 변경을 감지했을 때 now()로 갱신한다.
  content_updated_at timestamptz not null default now(),

  -- 원문 버전. 원문이나 파일 목록이 바뀔 때마다 원문 변경 trigger가 1씩 올린다.
  -- trigger는 notice_summaries migration에 있다.
  -- 요약 실행은 시작할 때의 버전과 저장 시점의 버전이 같을 때만 결과를 저장한다.
  content_revision bigint not null default 1
    constraint notices_content_revision_ck check (content_revision > 0),

  -- 같은 글은 출처·게시판/분야별 한 행. 서울시 분야가 다르면 같은 번호도 별개다.
  -- 동 게시판에서 같은 post_sn이 고정 행과 번호 행에 모두 나오면
  -- pipeline이 한 행으로 합치고 is_pinned = true로 둔다.
  constraint notices_post_uq      unique (category, source_board, post_sn),

  -- 아래 check들은 이름을 붙여 두었다.
  -- pipeline에서 psycopg 예외를 잡을 때 제약 이름으로 원인을 구분할 수 있다.
  constraint notices_category_ck  check (category in ('nowon', 'dong', 'seoul')),

  constraint notices_source_board_ck check (
    (category = 'nowon' and source_board = '1001')
    or (category = 'dong' and source_board = '1042')
    or (category = 'seoul' and source_board in ('21','22','23','24','25','26','27','30'))
  ),

  constraint notices_dong_group_ck check (dong_group in ('wolgye1', 'other')),

  constraint notices_license_ck   check (license_type in ('KOGL-1', 'KOGL-2', 'KOGL-3', 'KOGL-4')),

  -- 출처별로 컬럼 조합이 맞는지 강제한다.
  -- 노원구·서울시 공지에는 동 정보가 없고 고정 개념도 없다.
  -- 동 공지에는 어느 동인지가 반드시 있어야 한다.
  -- pipeline 버그로 분류가 섞이면 조용히 저장되는 대신 여기서 막힌다.
  constraint notices_shape_ck     check (
    (category in ('nowon', 'seoul') and dong_group is null and is_pinned = false)
    or (category = 'dong' and dong_group is not null)
  )
);

-- 앱의 기본 조회: 보이는 글을 최신순으로.
--
-- partial index(where is_visible)인 이유: is_visible은 대부분 true라
-- 선행 컬럼으로 두면 변별력이 없다. 숨긴 행을 인덱스에서 아예 빼는 쪽이 작고 빠르다.
--
-- id desc를 붙인 이유: registered_on이 date라 같은 날짜 글이 많다.
-- tiebreaker가 없으면 페이지네이션에서 행이 중복되거나 빠질 수 있다.
--
-- 고정 공지를 상단에 올릴 거라면 (is_pinned desc, registered_on desc, id desc)가
-- 되어야 한다. 화면 설계가 정해진 뒤 migration 한 줄로 바꾼다.
create index notices_visible_idx
  on notices (registered_on desc, id desc)
  where is_visible;


-- ----------------------------------------------------------------------------
-- notice_files: 공지에 딸린 첨부파일과 본문 이미지
--
-- 공지가 수정되면 해당 공지의 행을 모두 지우고 새 목록으로 다시 넣는다.
-- 이 교체는 동기화 transaction 안에서 일어나므로, 중간에 실패하면
-- 기존 파일 목록이 그대로 남는다.
-- ----------------------------------------------------------------------------
create table notice_files (
  id         bigint generated always as identity primary key,

  -- 공지가 지워지면 파일도 같이 지운다.
  -- 실제로 공지를 delete하는 경로는 없지만(숨김으로 처리한다), 고아 행을 막는다.
  notice_id  bigint not null references notices(id) on delete cascade,

  -- 'attachment' = 첨부 목록의 파일, 'inline_image' = 본문 <img>.
  -- 본문과 확인된 원문 첨부 영역에서 추출한 참조를 저장한다.
  kind       text   not null,

  -- 실제 q_fileSn(파일 집합 번호). 중복 가능하며 출처에 없으면 null.
  file_sn    text,

  -- 실제 q_fileId. 출처에 없으면 null이며 가짜 UUID를 만들지 않는다.
  file_id    text,

  -- 실제 ID가 있으면 id:<file_id>, 없으면 url:<저장 URL의 UTF-8 SHA256>.
  -- URL 정규화는 저장 코드의 책임이다. 파일 내용 해시가 아니다.
  file_key   text   not null,

  -- 첨부 목록의 표시 이름. 본문 이미지는 이름이 없어 null.
  -- "직권조치결과공고문(이0진).pdf"처럼 마스킹된 성명이 들어갈 수 있어
  -- 앱에 노출하지 않는다. 차단은 이 파일 아래쪽의 컬럼 단위 grant로 한다.
  file_name  text,

  -- 다운로드 주소 전체.
  -- 본문 <img>의 src에서 읽을 때 주소 안의 &가 &amp;로 들어 있으므로
  -- 정규식이 아니라 HTML parser로 src를 얻고 urllib.parse로 query를 나눈다.
  url        text   not null,

  -- 같은 파일의 첨부·본문 이미지 역할은 각각 보존하고 같은 역할만 중복 차단.
  constraint notice_files_identity_uq unique (notice_id, file_key, kind),

  constraint notice_files_identifiers_ck check (
    (file_sn is null or (file_sn = btrim(file_sn) and length(file_sn) > 0))
    and (file_id is null or (file_id = btrim(file_id) and length(file_id) > 0))
  ),
  constraint notice_files_url_nonblank_ck check (url = btrim(url) and length(url) > 0),
  constraint notice_files_key_ck check (
    file_key = case when file_id is not null then 'id:' || file_id
      else 'url:' || encode(sha256(convert_to(url, 'UTF8')), 'hex') end
  ),

  constraint notice_files_kind_ck check (kind in ('attachment', 'inline_image'))
);

-- notice_id 단독 인덱스는 두지 않는다.
-- notice_files_identity_uq가 notice_id를 선행 컬럼으로 하는 인덱스를 이미 만들어서
-- "이 공지의 파일 전부" 조회를 그 인덱스가 처리한다.


-- ============================================================================
-- 앱 읽기 권한과 backend 권한
--
-- 앱은 보이는 공지와 그 파일의 일부 컬럼만 읽고, 쓰기는 전혀 할 수 없다.
-- pipeline은 DB 소유 계정(connection string)으로 직접 연결하므로
-- 아래 규칙의 영향을 받지 않는다.
--
-- Postgres의 접근 제어는 2층이고, 둘 다 통과해야 읽힌다.
--   1층 GRANT  - 테이블/컬럼 단위. 자격이 없으면 쿼리 자체가 거부된다.
--   2층 POLICY - 행 단위. 조건에 맞는 행만 남는다. 에러 없이 조용히 걸러진다.
--
-- nowon/dong/seoul에 동일한 공개 상태 규칙을 적용한다.
-- source_board는 공지 조회로 공개하지만 file_key는 파일 허용 컬럼에 넣지 않는다.
-- Supabase는 public 스키마의 새 테이블과 sequence에 anon, authenticated의 모든 권한을
-- 자동으로 준다. 아래 회수가 빠지면 숨긴 공지와 file_name이 전부 공개된다.
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


-- id sequence: 앱은 쓰지 않으므로 Supabase가 자동으로 준 권한을 회수한다.
revoke all on sequence public.notices_id_seq, public.notice_files_id_seq
  from public, anon, authenticated;


-- notices, notice_files: service_role에 BYPASSRLS가 없는 환경에서도 backend가 쓸 수 있게 명시한다.
grant select, insert, update, delete on public.notices, public.notice_files to service_role;
grant usage, select on public.notices_id_seq, public.notice_files_id_seq to service_role;
create policy "service role manages notice sources"
  on public.notices for all to service_role using (true) with check (true);
create policy "service role manages notice source files"
  on public.notice_files for all to service_role using (true) with check (true);


-- ============================================================================
-- 설명(comment)
-- ============================================================================
comment on column public.notices.source_board is
  'Source board: Nowon 1001, dong board 1042, SeoulNewsList BLOG_ID. Not dong_group.';
comment on column public.notices.post_sn is
  'Source post ID as text: Nowon ID, dong q_bbscttSn, Seoul POST_ID. Preserve leading zeros.';
comment on column public.notice_files.file_key is
  'id:<actual file_id>, or url:<SHA256 hex of stored normalized URL UTF-8> if file_id is NULL.';
comment on column public.notice_files.file_id is
  'Actual source file ID; NULL when absent. Do not invent a UUID.';
comment on column public.notice_files.file_sn is
  'Actual source file group/serial number; NULL when absent, not a uniqueness key.';
comment on column public.notices.content_updated_at is
  'Last actual notice content/file-list change. Unchanged collection preserves this time.';
comment on column public.notices.content_revision is
  'Monotonic source version. Actual source/attachment metadata changes invalidate public summaries immediately; collection-only updates preserve it.';
