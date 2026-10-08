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
                     registered_on, url, body_html, body_text, license_type, is_visible) values

  -- 노원 공지: dong_group은 null, is_pinned는 false여야 한다 (notices_shape_ck)
  ('nowon', '1001', null, false, '20260901000000001', '테스트 노원 공지',        '테스트과',
   '2026-09-01', 'https://www.nowon.kr/test/1', '<p>본문</p>', '본문', 'KOGL-4', true),

  -- 월계1동 일반 공지: 본문이 없는 경우 (첨부만 있는 공지 모사)
  ('dong', '1042', 'wolgye1', false, '20260901000000002', '테스트 월계1동 공지',     '월계1동 테스트팀',
   '2026-09-01', 'https://www.nowon.kr/test/2', null,          null, 'KOGL-1', true),

  -- 월계1동 고정 공지
  ('dong', '1042', 'wolgye1', true,  '20260901000000003', '테스트 월계1동 고정 공지', '월계1동 테스트팀',
   '2026-09-01', 'https://www.nowon.kr/test/3',
   '<p>월계1동 복지 상담 운영 안내. 월계1동 주민 대상. 월계1동 동주민센터에서 상담받을 수 있습니다.</p>',
   '월계1동 복지 상담 운영 안내. 월계1동 주민 대상. 월계1동 동주민센터에서 상담받을 수 있습니다.',
   'KOGL-1', true),

  -- 다른 동 고정 공지: 고정 상태라 보인다
  ('dong', '1042', 'other',   true,  '20260901000000004', '테스트 다른 동 고정 공지', '테스트동',
   '2026-09-01', 'https://www.nowon.kr/test/4', null,          null, 'KOGL-1', true),

  -- 다른 동인데 고정이 풀린 글: 숨김 규칙에 따라 is_visible = false
  ('dong', '1042', 'other',   false, '20260901000000005', '테스트 고정 해제 공지',   '테스트동',
   '2026-09-01', 'https://www.nowon.kr/test/5',
   '<p>월계1동 복지 상담 운영 안내. 월계1동 주민 대상. 월계1동 동주민센터에서 상담받을 수 있습니다.</p>',
   '월계1동 복지 상담 운영 안내. 월계1동 주민 대상. 월계1동 동주민센터에서 상담받을 수 있습니다.',
   'KOGL-1', false);


-- 파일 4건.
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

-- 4) 일부 파일만 읽은 상태를 표현할 두 번째 월계1동 첨부파일.
insert into notice_files (notice_id, kind, file_sn, file_id, file_key, file_name, url)
select n.id, 'attachment', '2', 'aaaaaaaa-0000-0000-0000-000000000004',
       'id:aaaaaaaa-0000-0000-0000-000000000004', '테스트추가첨부.pdf',
       'https://www.nowon.kr/component/file/ND_fileDownload.do?q_fileSn=2&q_fileId=aaaaaaaa-0000-0000-0000-000000000004'
from notices n where n.category = 'dong' and n.source_board = '1042'
  and n.post_sn = '20260901000000002';

-- #14: 네 상태와 부분 읽기, 숨김 공지의 요약을 함께 확인한다.
-- 생성 결과가 없는 기존 검토 행과 최초 실패/대기는 요약 없이 유지한다.
insert into notice_summaries
  (notice_id, status, attachment_status, source_hash, model, prompt_version,
   attempt_count, last_error_code, generated_at)
select n.id, case n.post_sn
    when '20260901000000001' then 'pending'
    when '20260901000000002' then 'needs_review'
    else 'failed' end,
  case n.post_sn when '20260901000000001' then 'none'
    when '20260901000000002' then 'partial' else 'unread' end,
  repeat('a', 64), 'gemini-2.5-flash', 'seed-summary-v2',
  case n.post_sn when '20260901000000001' then 0 else 1 end,
  case n.post_sn when '20260901000000004' then 'api_timeout' else null end,
  case n.post_sn when '20260901000000002' then '2026-09-01T00:00:00Z'::timestamptz else null end
from notices n where n.post_sn in
  ('20260901000000001', '20260901000000002', '20260901000000004');

-- 완전한 NoticeSummary JSON과 확인한 텍스트 근거의 예시.
-- 마지막 공지는 숨김이므로 동일한 public result가 있어도 앱에는 조회되지 않는다.
insert into notice_summaries
  (notice_id, status, result, category, category_code, attachment_status,
   source_hash, model, prompt_version, attempt_count, generated_at)
select n.id, 'summarized',
  '{
    "category": "living", "category_code": 27,
    "summary": "월계1동 복지 상담 운영 안내", "publisher": null,
    "applicable_area": "월계1동", "audience": "월계1동 주민",
    "audience_scope": "general", "action": null, "action_requirement": "none",
    "location": "월계1동 동주민센터", "dates": [], "status": "not_applicable",
    "status_detail": null, "notice_update": "new", "changed_details": null,
    "notes": [], "topics": [], "uncertainties": [],
    "evidence": [
      {"field": "category", "excerpt": "복지 상담 운영 안내", "source_type": "text", "source_id": null, "page": null, "verification": "text_matched"},
      {"field": "category_code", "excerpt": "복지 상담", "source_type": "text", "source_id": null, "page": null, "verification": "text_matched"},
      {"field": "summary", "excerpt": "월계1동 복지 상담 운영 안내", "source_type": "text", "source_id": null, "page": null, "verification": "text_matched"},
      {"field": "applicable_area", "excerpt": "월계1동", "source_type": "text", "source_id": null, "page": null, "verification": "text_matched"},
      {"field": "audience", "excerpt": "월계1동 주민 대상", "source_type": "text", "source_id": null, "page": null, "verification": "text_matched"},
      {"field": "location", "excerpt": "월계1동 동주민센터", "source_type": "text", "source_id": null, "page": null, "verification": "text_matched"}
    ]
  }'::jsonb, 'living', 27,
  case n.post_sn when '20260901000000005' then 'all_read' else 'none' end,
  repeat('b', 64), 'gemini-2.5-flash', 'seed-summary-v2',
  1, '2026-09-01T00:00:00Z'::timestamptz
from notices n where n.post_sn in ('20260901000000003', '20260901000000005');

-- ============================================================================
-- #58: 앱 화면 상태별 공지 (6~11번)
--
-- 앱 연결 계획 4장의 화면 상태를 하나씩 담는다. 요약과 쉬운말 행은 손으로 쓰지 않았다.
-- 아래 공지를 빈 DB에 넣고 pipeline summarize-one과 쉬운말 처리를 Gemini, 파일 다운로드
-- 대역으로 실행한 뒤 저장된 행을 그대로 옮겼다(기준 시각 2026-09-06 09:00 KST).
-- file_manifest의 notice_id와 파일 id는 db reset 순서에서 정해지는 값이므로,
-- 위쪽 공지와 파일의 삽입 순서를 바꾸면 이 구역을 다시 생성해야 한다.
--
--   6  노원구   summarized, 카드 4종, 마감일 2026-09-30, 쉬운말 있음
--   7  월계1동  needs_review, 첨부 일부 누락(partial): PDF는 근거 링크, .hwp(HWPX)는 누락 안내
--   8  서울시   needs_review, 분야 미분류(category_code null)
--   9  노원구   failed (Gemini 오류)
--   10 월계1동  요약 없음(display_status = none), 쉬운말 있음
--   11 노원구   요약 없음, 쉬운말은 이전 본문 기준이라 앱에 보이지 않음
-- ============================================================================

insert into notices (category, source_board, dong_group, is_pinned, post_sn, title, department,
                     registered_on, url, body_html, body_text, license_type, is_visible) values
  ('nowon', '1001', null, false, '20260901000000006', '가을 독서 프로그램 참가자 모집', '교육지원과',
   '2026-09-01', 'https://www.nowon.kr/test/6',
   '<p>가을 독서 프로그램 참가자 모집</p><p>■ 모집대상 : 노원구 거주 성인</p><p>■ 신청기간 : 2026. 9. 1. ~ 9. 30.</p><p>■ 운영장소 : 노원구립도서관 강의실</p><p>■ 신청방법 : 도서관 홈페이지에서 신청</p><p>■ 참가비 : 무료</p>',
   '가을 독서 프로그램 참가자 모집
■ 모집대상 : 노원구 거주 성인
■ 신청기간 : 2026. 9. 1. ~ 9. 30.
■ 운영장소 : 노원구립도서관 강의실
■ 신청방법 : 도서관 홈페이지에서 신청
■ 참가비 : 무료', 'KOGL-4', true),
  ('dong', '1042', 'wolgye1', false, '20260901000000007', '월계1동 주민자치 프로그램 수강생 모집', '월계1동 테스트팀',
   '2026-09-02', 'https://www.nowon.kr/test/7',
   '<p>월계1동 주민자치 프로그램 수강생을 모집합니다.</p><p>■ 모집대상 : 월계1동 주민</p><p>■ 접수장소 : 월계1동 주민센터</p><p>■ 자세한 내용은 첨부파일을 확인하세요.</p>',
   '월계1동 주민자치 프로그램 수강생을 모집합니다.
■ 모집대상 : 월계1동 주민
■ 접수장소 : 월계1동 주민센터
■ 자세한 내용은 첨부파일을 확인하세요.', 'KOGL-1', true),
  ('seoul', '26', null, false, '000901', '서울 문화의 밤 행사 안내', '테스트 문화과',
   '2026-09-03', 'https://news.seoul.go.kr/culture/archives/000901',
   '<p>서울 문화의 밤 행사를 안내합니다.</p><p>■ 일시 : 2026. 9. 20. 19:00</p><p>■ 장소 : 서울광장</p>',
   '서울 문화의 밤 행사를 안내합니다.
■ 일시 : 2026. 9. 20. 19:00
■ 장소 : 서울광장', null, true),
  ('nowon', '1001', null, false, '20260901000000009', '도로 보수 공사 안내', '도로관리과',
   '2026-09-04', 'https://www.nowon.kr/test/9',
   '<p>도로 보수 공사를 안내합니다.</p><p>■ 공사기간 : 2026. 9. 7. ~ 9. 11.</p>',
   '도로 보수 공사를 안내합니다.
■ 공사기간 : 2026. 9. 7. ~ 9. 11.', 'KOGL-4', true),
  ('dong', '1042', 'wolgye1', false, '20260901000000010', '월계1동 경로당 프로그램 안내', '월계1동 테스트팀',
   '2026-09-05', 'https://www.nowon.kr/test/10',
   '<p>월계1동 경로당 프로그램 참가자를 모집합니다.</p><p>■ 대상 : 월계1동 어르신</p>',
   '월계1동 경로당 프로그램 참가자를 모집합니다.
■ 대상 : 월계1동 어르신', 'KOGL-1', true),
  ('nowon', '1001', null, false, '20260901000000011', '공영주차장 이용 안내', '주차행정과',
   '2026-09-05', 'https://www.nowon.kr/test/11',
   '<p>공영주차장 이용 시간이 변경됩니다.</p><p>■ 변경 후 : 07:00 ~ 22:00</p>',
   '공영주차장 이용 시간이 변경됩니다.
■ 변경 후 : 07:00 ~ 22:00', 'KOGL-4', true);

insert into notice_files (notice_id, kind, file_sn, file_id, file_key, file_name, url)
select n.id, 'attachment', '7', 'aaaaaaaa-0000-0000-0000-000000000007', 'id:aaaaaaaa-0000-0000-0000-000000000007', '프로그램안내.pdf',
       'https://www.nowon.kr/component/file/ND_fileDownload.do?q_fileSn=7&q_fileId=aaaaaaaa-0000-0000-0000-000000000007'
from notices n where n.post_sn = '20260901000000007';

insert into notice_files (notice_id, kind, file_sn, file_id, file_key, file_name, url)
select n.id, 'attachment', '7', 'aaaaaaaa-0000-0000-0000-000000000008', 'id:aaaaaaaa-0000-0000-0000-000000000008', '수강신청서.hwp',
       'https://www.nowon.kr/component/file/ND_fileDownload.do?q_fileSn=7&q_fileId=aaaaaaaa-0000-0000-0000-000000000008'
from notices n where n.post_sn = '20260901000000007';

insert into notice_summaries (notice_id, status, result, category, category_code, deadline_on, attachment_status, source_hash, model, prompt_version, attempt_count, last_error_code, generated_at, file_manifest)
select n.id, 'summarized', '{"action": "도서관 홈페이지에서 신청", "action_requirement": "optional", "applicable_area": null, "audience": "노원구 거주 성인", "audience_scope": "conditional", "card_summaries": {"action": "도서관 홈페이지에서 신청해요.", "audience": "노원구에 사는 성인이 신청할 수 있어요.", "deadline": "9월 30일까지 신청해요.", "notes": "참가비는 무료예요."}, "category": "application", "category_code": 26, "changed_details": null, "dates": [{"end_date": "2026-09-30", "end_time": null, "kind": "application", "label": "신청기간", "start_date": "2026-09-01", "start_time": null, "text": "2026. 9. 1. ~ 9. 30."}], "evidence": [{"excerpt": "가을 독서 프로그램 참가자 모집", "field": "summary", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "가을 독서 프로그램 참가자 모집", "field": "category", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "가을 독서 프로그램", "field": "category_code", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "■ 모집대상 : 노원구 거주 성인", "field": "audience", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "■ 모집대상 : 노원구 거주 성인", "field": "audience_scope", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "■ 신청방법 : 도서관 홈페이지에서 신청", "field": "action", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "■ 신청방법 : 도서관 홈페이지에서 신청", "field": "action_requirement", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "■ 운영장소 : 노원구립도서관 강의실", "field": "location", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "■ 신청기간 : 2026. 9. 1. ~ 9. 30.", "field": "dates", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "■ 신청기간 : 2026. 9. 1. ~ 9. 30.", "field": "status", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "■ 참가비 : 무료", "field": "notes", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}], "location": "노원구립도서관 강의실", "notes": ["참가비 : 무료"], "notice_update": "new", "publisher": null, "status": "open", "status_detail": null, "summary": "가을 독서 프로그램 참가자 모집", "topics": [], "uncertainties": []}'::jsonb, 'application', 26, '2026-09-30'::date, 'none', 'b6926a24c6305aacc558197dfde231d8017db004e8b7a8e1c0f4ae7ed6fb8ec7', 'gemini-3.5-flash-lite', 'notice-summary-v6-card-grounding', 1, null, '2026-09-06T00:00:00+00:00'::timestamptz, '{"files": [], "media": [], "notice_id": 6, "omissions": [], "original_url": "https://www.nowon.kr/test/6", "source_revision": 1}'::jsonb
from notices n where n.post_sn = '20260901000000006';

insert into notice_summaries (notice_id, status, result, category, category_code, deadline_on, attachment_status, source_hash, model, prompt_version, attempt_count, last_error_code, generated_at, file_manifest)
select n.id, 'needs_review', '{"action": null, "action_requirement": "unknown", "applicable_area": null, "audience": "월계1동 주민", "audience_scope": "conditional", "card_summaries": {"action": "월계1동 주민센터에서 접수해요.", "audience": "월계1동 주민이 대상이에요.", "deadline": null, "notes": null}, "category": "application", "category_code": 26, "changed_details": null, "dates": [], "evidence": [{"excerpt": "월계1동 주민자치 프로그램 수강생을 모집합니다.", "field": "summary", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "수강생을 모집합니다.", "field": "category", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "주민자치 프로그램", "field": "category_code", "page": null, "source_id": "media_1", "source_type": "document", "verification": null}, {"excerpt": "■ 모집대상 : 월계1동 주민", "field": "audience", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "■ 모집대상 : 월계1동 주민", "field": "audience_scope", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "■ 접수장소 : 월계1동 주민센터", "field": "location", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}], "location": "월계1동 주민센터", "notes": [], "notice_update": "new", "publisher": null, "status": "unknown", "status_detail": null, "summary": "월계1동 주민자치 프로그램 수강생 모집", "topics": [], "uncertainties": ["원문 확인 필요", "일부 첨부 미확인"]}'::jsonb, 'application', 26, null, 'partial', 'f22de398d5bd204df3d70b93e1e9f55ddf0758311b3c3cb2a8b8a6476940ec0f', 'gemini-3.5-flash-lite', 'notice-summary-v6-card-grounding', 1, 'input_preparation_failed', '2026-09-06T00:00:00+00:00'::timestamptz, '{"files": [{"attachment_index": null, "content_sha256": "794abaa4f6f06fc519895c22944a0ab43ad02b4fb32bdefa1952ce81613cb47b", "file_key": "id:aaaaaaaa-0000-0000-0000-000000000007", "kind": "attachment", "notice_file_id": 5, "outcome": "media", "source_id": "media_1", "url": "https://www.nowon.kr/component/file/ND_fileDownload.do?q_fileSn=7&q_fileId=aaaaaaaa-0000-0000-0000-000000000007"}, {"attachment_index": null, "content_sha256": null, "file_key": "id:aaaaaaaa-0000-0000-0000-000000000008", "kind": "attachment", "notice_file_id": 6, "outcome": "unread", "source_id": null, "url": "https://www.nowon.kr/component/file/ND_fileDownload.do?q_fileSn=7&q_fileId=aaaaaaaa-0000-0000-0000-000000000008"}], "media": [{"content_sha256": "794abaa4f6f06fc519895c22944a0ab43ad02b4fb32bdefa1952ce81613cb47b", "input_block_index": 1, "source_id": "media_1", "source_type": "document"}], "notice_id": 7, "omissions": [{"notice_file_id": 6, "reason_code": "type_mismatch", "url": "https://www.nowon.kr/component/file/ND_fileDownload.do?q_fileSn=7&q_fileId=aaaaaaaa-0000-0000-0000-000000000008"}], "original_url": "https://www.nowon.kr/test/7", "source_revision": 3}'::jsonb
from notices n where n.post_sn = '20260901000000007';

insert into notice_summaries (notice_id, status, result, category, category_code, deadline_on, attachment_status, source_hash, model, prompt_version, attempt_count, last_error_code, generated_at, file_manifest)
select n.id, 'needs_review', '{"action": null, "action_requirement": "none", "applicable_area": null, "audience": null, "audience_scope": "unknown", "card_summaries": {"action": "서울광장에서 열려요.", "audience": null, "deadline": "9월 20일 19:00에 열려요.", "notes": null}, "category": "event", "category_code": null, "changed_details": null, "dates": [{"end_date": "2026-09-20", "end_time": null, "kind": "event", "label": "일시", "start_date": "2026-09-20", "start_time": "19:00", "text": "2026. 9. 20. 19:00"}], "evidence": [{"excerpt": "서울 문화의 밤 행사를 안내합니다.", "field": "summary", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "서울 문화의 밤 행사를 안내합니다.", "field": "category", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "■ 장소 : 서울광장", "field": "location", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "■ 일시 : 2026. 9. 20. 19:00", "field": "dates", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}, {"excerpt": "■ 일시 : 2026. 9. 20. 19:00", "field": "status", "page": null, "source_id": null, "source_type": "text", "verification": "text_matched"}], "location": "서울광장", "notes": [], "notice_update": "new", "publisher": null, "status": "upcoming", "status_detail": null, "summary": "서울 문화의 밤 행사 안내", "topics": [], "uncertainties": ["행사 분야 확인 필요", "원문 확인 필요"]}'::jsonb, 'event', null, null, 'none', '92a0f38d6e28dbac1a2eb34223ae1778b767d6ee536a76774d1b9cd499cbd2e7', 'gemini-3.5-flash-lite', 'notice-summary-v6-card-grounding', 1, null, '2026-09-06T00:00:00+00:00'::timestamptz, '{"files": [], "media": [], "notice_id": 8, "omissions": [], "original_url": "https://news.seoul.go.kr/culture/archives/000901", "source_revision": 1}'::jsonb
from notices n where n.post_sn = '000901';

insert into notice_summaries (notice_id, status, result, category, category_code, deadline_on, attachment_status, source_hash, model, prompt_version, attempt_count, last_error_code, generated_at, file_manifest)
select n.id, 'failed', null, null, null, null, 'none', '44c30f3059446738211cccdf5b115d589c6782bfdf5525a008c6728580491d21', 'gemini-3.5-flash-lite', 'notice-summary-v6-card-grounding', 1, 'api_error', null, null
from notices n where n.post_sn = '20260901000000009';

insert into notice_easy_texts (notice_id, notice_revision, source_hash, original_text, easy_text, changes, model, prompt_version, attempt_count, generated_at, body_text_present, attachment_content_included)
select n.id, '91426dd1b2c7890e36363d34e73d5ad4416f1d489b146ad0a17965c317bfbc2c', '6ceaf57e617c4f564c7c76655bfc28e66331678a7ed089d28b169344315ee2d1', '가을 독서 프로그램 참가자 모집
가을 독서 프로그램 참가자 모집
■ 모집대상 : 노원구 거주 성인
■ 신청기간 : 2026. 9. 1. ~ 9. 30.
■ 운영장소 : 노원구립도서관 강의실
■ 신청방법 : 도서관 홈페이지에서 신청
■ 참가비 : 무료', '가을 독서 프로그램 참가자 모집
가을 독서 프로그램 참가자 모집
■ 모집대상 : 노원구 거주 성인
■ 신청기간 : 2026. 9. 1. ~ 9. 30.
■ 운영장소 : 노원구립도서관 강의실
■ 신청방법 : 도서관 홈페이지에서 신청
■ 내는 돈 : 무료', '[{"context": "■ 참가비 : 무료", "end": 134, "original": "참가비", "replacement": "내는 돈", "start": 131}]'::jsonb, 'gemini-3.5-flash-lite', 'easy-language-v7', 1, '2026-09-06T00:00:00+00:00'::timestamptz, true, false
from notices n where n.post_sn = '20260901000000006';

insert into notice_easy_texts (notice_id, notice_revision, source_hash, original_text, easy_text, changes, model, prompt_version, attempt_count, generated_at, body_text_present, attachment_content_included)
select n.id, '5318461043ffe0c755ac618a6a4abbda933c642cfc1c5deb55b1ccb44e56337d', '7589acfcf2f595c6dffc9a417dc086fa40cc01127f90cd588ff47698437b02ea', '월계1동 경로당 프로그램 안내
월계1동 경로당 프로그램 참가자를 모집합니다.
■ 대상 : 월계1동 어르신', '월계1동 경로당 프로그램 안내
월계1동 경로당 프로그램 참가자를 모집합니다.
■ 대상 : 월계1동 노인', '[{"context": "■ 대상 : 월계1동 어르신", "end": 58, "original": "어르신", "replacement": "노인", "start": 55}]'::jsonb, 'gemini-3.5-flash-lite', 'easy-language-v7', 1, '2026-09-06T00:00:00+00:00'::timestamptz, true, false
from notices n where n.post_sn = '20260901000000010';

insert into notice_easy_texts (notice_id, notice_revision, source_hash, original_text, easy_text, changes, model, prompt_version, attempt_count, generated_at, body_text_present, attachment_content_included)
select n.id, '86e0613449bf6bee54aefad951855b1ceef2d363ed5b3134b901f7013a13e2e9', 'f25eaa230863345394c50c304efb8a137c4720bb57e6d84cbf9c77e17bcf311a', '공영주차장 이용 안내
공영주차장 이용 시간을 안내합니다.
■ 이용 시간 : 08:00 ~ 21:00', '공영주차장 이용 안내
공영주차장 이용 시간을 안내합니다.
■ 쓸 수 있는 시간 : 08:00 ~ 21:00', '[{"context": "■ 이용 시간 : 08:00 ~ 21:00", "end": 39, "original": "이용 시간", "replacement": "쓸 수 있는 시간", "start": 34}]'::jsonb, 'gemini-3.5-flash-lite', 'easy-language-v7', 1, '2026-09-06T00:00:00+00:00'::timestamptz, true, false
from notices n where n.post_sn = '20260901000000011';
