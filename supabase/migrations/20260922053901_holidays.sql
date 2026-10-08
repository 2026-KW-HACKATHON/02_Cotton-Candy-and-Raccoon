-- ============================================================================
-- holidays: 공휴일
--
-- 수집 스케줄을 공휴일에 맞추려고 만든 테이블이다. 현재 pipeline은 이 테이블을
-- 읽거나 쓰지 않는다. 앱도 접근할 수 없다.
-- ============================================================================
create table holidays (
  -- 공휴일 날짜. API의 locdate(8자리, 예: 20261003)를 date로 변환해 저장한다.
  -- 날짜당 한 행이다. 같은 날에 명칭이 둘 이상 오는 경우는 upsert가 나중 값으로 덮는다.
  -- 판단에 쓰이는 값은 is_holiday 하나뿐이라 명칭이 바뀌어도 동작에 영향이 없다.
  locdate    date        primary key,

  -- API의 dateName. 예: '개천절', '대체공휴일', '추석'.
  -- 로그와 디버깅용이다. 로직에서 이 값으로 분기하지 않는다.
  date_name  text        not null,

  -- API의 isHoliday('Y'/'N')를 boolean으로 변환.
  -- getRestDeInfo는 공휴일만 반환하므로 사실상 항상 true지만,
  -- 값을 그대로 저장해 두어야 API 쪽 변화가 생겼을 때 드러난다.
  is_holiday boolean     not null,

  -- 이 행을 마지막으로 받아온 시각.
  -- 갱신이 멈췄는지 판단하는 근거다. upsert마다 now()로 갱신한다.
  fetched_at timestamptz not null default now()
);


-- 앱은 읽지 않는다. RLS를 켜고 policy 없이 테이블 권한까지 회수한다.
alter table holidays enable row level security;

revoke all on holidays from anon, authenticated;

-- ============================================================================
-- 설명(comment)
-- ============================================================================
comment on table holidays is
  '특일 정보 API로 받은 공휴일. 수집 스케줄 판단용으로 만들었으며 현재 pipeline은 읽지 않는다. 앱은 접근할 수 없다.';
