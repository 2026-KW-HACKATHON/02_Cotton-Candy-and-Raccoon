-- ============================================================================
-- notices, notice_files: 수집한 공지를 그대로 저장하는 테이블
--
-- 범위: 노원구 공지 API와 동주민센터 게시판에서 받은 내용만 저장한다.
--       LLM 요약 결과, 수집 이력, 첨부파일 추출 상태는 여기 두지 않는다.
--       해당 단계를 만들 때 별도 테이블로 추가한다.
--
-- 쓰기: pipeline(Python)이 psycopg로 직접 연결해 넣는다. RLS 영향을 받지 않는다.
-- 읽기: 앱(Expo)이 publishable key로 읽는다. 권한은 rls migration에서 정한다.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- notices: 공지 한 건이 한 행
-- ----------------------------------------------------------------------------
create table notices (
  -- DB 내부 번호. 공지 자체의 번호가 아니라 이 테이블의 식별자다.
  -- notice_files가 이 값을 참조한다.
  id             bigint      generated always as identity primary key,

  -- 출처 구분. 'nowon' = 노원구 공지 API, 'dong' = 동주민센터 게시판.
  -- 두 출처가 post_sn 번호 체계를 공유하지 않으므로 유일 키에 함께 들어간다.
  category       text        not null,

  -- 동 공지가 어느 동 것인지. 'wolgye1' = 월계1동, 'other' = 다른 동.
  -- 노원 공지에는 해당이 없어 null.
  -- 판단 기준: 번호 붙은 행은 월계1동 목록에서 왔으므로 wolgye1,
  --            고정 행은 부서 표기가 "월계1동"으로 시작하면 wolgye1, 아니면 other.
  dong_group     text,

  -- 목록 상단 "공지" 행으로 올라와 있는지.
  -- 다른 동 글은 고정이 풀리면 볼 이유가 없어져 is_visible도 false가 된다.
  is_pinned      boolean     not null default false,

  -- 게시물 번호 (17자리). 노원 API는 ID 필드, 동 게시판은 URL의 q_bbscttSn.
  -- 앞자리 0이 의미를 가질 수 있어 숫자형이 아니라 text로 둔다.
  post_sn        text        not null,

  title          text        not null,

  -- 부서 표기를 받은 그대로 저장한다. dong_group 판단에 쓰인다.
  -- 노원 API의 MANAGER는 sample 5건 모두 DEPARTMENT와 같아서 저장하지 않는다.
  department     text,

  -- 등록일 (KST). 시각 없이 날짜만 제공되므로 date.
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
  -- 동 게시판은 게시물 하단 표시값을 그대로 쓴다.
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

  -- pipeline이 덮어쓸 때 now()로 직접 갱신한다. trigger는 두지 않는다.
  -- 동기화가 조용히 멈췄을 때 이 값으로 알아차린다.
  updated_at     timestamptz not null default now(),

  -- 같은 글은 분류당 한 행만. 출처가 다르면 번호가 겹쳐도 별개 행이다.
  -- 동 게시판에서 같은 post_sn이 고정 행과 번호 행에 모두 나오면
  -- pipeline이 한 행으로 합치고 is_pinned = true로 둔다.
  constraint notices_post_uq      unique (category, post_sn),

  -- 아래 check들은 이름을 붙여 두었다.
  -- pipeline에서 psycopg 예외를 잡을 때 제약 이름으로 원인을 구분할 수 있다.
  constraint notices_category_ck  check (category in ('nowon', 'dong')),

  constraint notices_dong_group_ck check (dong_group in ('wolgye1', 'other')),

  constraint notices_license_ck   check (license_type in ('KOGL-1', 'KOGL-2', 'KOGL-3', 'KOGL-4')),

  -- 출처별로 컬럼 조합이 맞는지 강제한다.
  -- 노원 공지에는 동 정보가 없고 고정 개념도 없다.
  -- 동 공지에는 어느 동인지가 반드시 있어야 한다.
  -- pipeline 버그로 분류가 섞이면 조용히 저장되는 대신 여기서 막힌다.
  constraint notices_shape_ck     check (
    (category = 'nowon' and dong_group is null and is_pinned = false)
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
  -- 노원 API에는 첨부 목록이 없어 inline_image만 생긴다.
  kind       text   not null,

  -- 다운로드 URL의 q_fileSn. 같은 공지 안에서 파일을 구분하는 번호.
  file_sn    text   not null,

  -- 다운로드 URL의 q_fileId (UUID). 다운로드에 필요하다.
  file_id    text   not null,

  -- 첨부 목록의 표시 이름. 본문 이미지는 이름이 없어 null.
  -- "직권조치결과공고문(이0진).pdf"처럼 마스킹된 성명이 들어갈 수 있어
  -- 앱에 노출하지 않는다. 차단은 rls migration의 컬럼 단위 grant로 한다.
  file_name  text,

  -- 다운로드 주소 전체.
  -- 본문 <img>의 src에서 읽을 때 주소 안의 &가 &amp;로 들어 있으므로
  -- 정규식이 아니라 HTML parser로 src를 얻고 urllib.parse로 query를 나눈다.
  url        text   not null,

  -- 같은 공지에 같은 파일 번호가 두 번 들어가지 않게 한다.
  --
  -- 주의: 같은 이미지가 본문에 두 번 나오는 공지가 있으면 두 번째 insert가
  -- 여기 걸려 동기화 transaction 전체가 롤백된다. pipeline에서 file_sn 기준으로
  -- 중복을 제거하거나 on conflict (notice_id, file_sn) do nothing을 붙일 것.
  constraint notice_files_sn_uq   unique (notice_id, file_sn),

  constraint notice_files_kind_ck check (kind in ('attachment', 'inline_image'))
);

-- notice_id 단독 인덱스는 두지 않는다.
-- notice_files_sn_uq가 notice_id를 선행 컬럼으로 하는 인덱스를 이미 만들어서
-- "이 공지의 파일 전부" 조회를 그 인덱스가 처리한다.
