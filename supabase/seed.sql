-- ============================================================================
-- 로컬 개발용 가짜 데이터
--
-- npx supabase db reset 이 migrations/ 를 전부 적용한 뒤 이 파일을 실행한다.
-- 원격 프로젝트(db push)에는 적용되지 않는다.
--
-- 실제 공지가 아니다. post_sn을 20260901000000001~5로 두어 실제 게시물 번호와
-- 겹치지 않게 했다. 분류 5가지와 권한 검증에 필요한 조합을 모두 포함한다.
-- ============================================================================


-- 분류 5가지.
-- 5번째 행은 is_visible = false이므로 앱에서 보이지 않아야 한다.
insert into notices (category, source_board, dong_group, is_pinned, post_sn, title, department,
                     registered_on, url, body_html, license_type, is_visible) values

  -- 노원 공지: dong_group은 null, is_pinned는 false여야 한다 (notices_shape_ck)
  ('nowon', '1001', null, false, '20260901000000001', '테스트 노원 공지',        '테스트과',
   '2026-09-01', 'https://www.nowon.kr/test/1', '<p>본문</p>', 'KOGL-4', true),

  -- 월계1동 일반 공지: 본문이 없는 경우 (첨부만 있는 공지 모사)
  ('dong', '1042', 'wolgye1', false, '20260901000000002', '테스트 월계1동 공지',     '월계1동 테스트팀',
   '2026-09-01', 'https://www.nowon.kr/test/2', null,          'KOGL-1', true),

  -- 월계1동 고정 공지
  ('dong', '1042', 'wolgye1', true,  '20260901000000003', '테스트 월계1동 고정 공지', '월계1동 테스트팀',
   '2026-09-01', 'https://www.nowon.kr/test/3', '<p>본문</p>', 'KOGL-1', true),

  -- 다른 동 고정 공지: 고정 상태라 보인다
  ('dong', '1042', 'other',   true,  '20260901000000004', '테스트 다른 동 고정 공지', '테스트동',
   '2026-09-01', 'https://www.nowon.kr/test/4', null,          'KOGL-1', true),

  -- 다른 동인데 고정이 풀린 글: 숨김 규칙에 따라 is_visible = false
  ('dong', '1042', 'other',   false, '20260901000000005', '테스트 고정 해제 공지',   '테스트동',
   '2026-09-01', 'https://www.nowon.kr/test/5', null,          'KOGL-1', false);


-- 파일 3건.
-- notices의 id는 identity라 값을 직접 쓰지 않고 post_sn으로 찾아서 넣는다.
-- db reset을 여러 번 해도 id가 달라지지 않게 하기 위해서다.

-- 1) 보이는 노원 공지의 본문 이미지. file_name은 본문 이미지라 null.
insert into notice_files (notice_id, kind, file_sn, file_id, file_key, file_name, url)
select n.id, 'inline_image', '1', 'aaaaaaaa-0000-0000-0000-000000000001', 'id:aaaaaaaa-0000-0000-0000-000000000001', null,
       'https://www.nowon.kr/component/file/ND_fileDownload.do?q_fileSn=1&q_fileId=aaaaaaaa-0000-0000-0000-000000000001'
from notices n where n.category = 'nowon' and n.source_board = '1001'
  and n.post_sn = '20260901000000001';

-- 2) 보이는 월계1동 일반 공지(본문 없음)의 첨부파일.
--    file_name에 마스킹된 성명 형태를 넣어 컬럼 권한 차단을 확인한다.
--    anon 역할로 select file_name 을 시도하면 permission denied 가 나야 한다.
insert into notice_files (notice_id, kind, file_sn, file_id, file_key, file_name, url)
select n.id, 'attachment', '2', 'aaaaaaaa-0000-0000-0000-000000000002', 'id:aaaaaaaa-0000-0000-0000-000000000002', '테스트첨부(이0진).pdf',
       'https://www.nowon.kr/component/file/ND_fileDownload.do?q_fileSn=2&q_fileId=aaaaaaaa-0000-0000-0000-000000000002'
from notices n where n.category = 'dong' and n.source_board = '1042'
  and n.post_sn = '20260901000000002';

-- 3) 숨긴 공지의 첨부파일.
--    anon 역할의 조회 결과에 이 행이 나오면 policy가 잘못된 것이다.
insert into notice_files (notice_id, kind, file_sn, file_id, file_key, file_name, url)
select n.id, 'attachment', '3', 'aaaaaaaa-0000-0000-0000-000000000003', 'id:aaaaaaaa-0000-0000-0000-000000000003', '숨긴공지첨부.pdf',
       'https://www.nowon.kr/component/file/ND_fileDownload.do?q_fileSn=3&q_fileId=aaaaaaaa-0000-0000-0000-000000000003'
from notices n where n.category = 'dong' and n.source_board = '1042'
  and n.post_sn = '20260901000000005';
