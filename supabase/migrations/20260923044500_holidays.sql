-- ============================================================================
-- holidays: 공공데이터포털 특일 정보 API로 받은 공휴일
--
-- 출처: 한국천문연구원_특일 정보, getRestDeInfo 오퍼레이션
--       https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo
--
-- 용도: 수집 실행 전에 오늘이 공휴일인지 판단한다.
--       공휴일이면 12시 실행만 통과시키고 나머지는 즉시 종료한다.
--
-- 갱신: 하루 한 번(12시 실행)만 API를 호출해 이번 달과 다음 달을 upsert한다.
--       나머지 실행은 이 테이블만 조회한다. API가 멈춰도 이미 받은 값으로 동작한다.
--
-- GitHub Actions는 실행마다 컨테이너를 새로 띄워 로컬 파일이 남지 않는다.
-- 그래서 파일 캐시가 아니라 DB에 둔다.
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

comment on table holidays is
  '특일 정보 API로 받은 공휴일. 수집 스케줄 게이트에 쓰인다. pipeline만 접근한다.';

-- 앱은 이 테이블을 읽지 않는다.
-- RLS를 켜고 policy를 만들지 않으면 anon, authenticated에게 아무 행도 보이지 않는다.
-- 테이블 권한까지 회수해 두면 나중에 policy를 잘못 추가해도 열리지 않는다.
-- pipeline은 DB 소유 계정으로 접속하므로 둘 다 영향을 받지 않는다.
alter table holidays enable row level security;

revoke all on holidays from anon, authenticated;

-- 조회 패턴은 "오늘이 공휴일인가" 한 가지다.
-- locdate가 primary key라 그 인덱스로 처리된다. 추가 인덱스는 두지 않는다.
