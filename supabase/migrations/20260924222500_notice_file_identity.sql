-- file_sn은 파일 식별 키에서 제외해 같은 번호를 가진 여러 파일을 허용한다.
-- 한 공지에서 같은 file_id가 첨부와 본문 이미지로 쓰이면 두 역할을 각각 보존한다.
-- 같은 file_id와 kind의 반복 참조는 중복으로 취급한다.
-- 이미 적용된 초기 마이그레이션은 수정하지 않고 제약만 교체한다.
alter table public.notice_files
  drop constraint notice_files_sn_uq,
  add constraint notice_files_identity_uq unique (notice_id, file_id, kind);
