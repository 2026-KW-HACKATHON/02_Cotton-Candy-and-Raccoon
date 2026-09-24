-- 같은 공지에서 file_sn이 같아도 file_id가 다르면 별도 파일로 보존한다.
-- 이미 적용된 초기 마이그레이션은 수정하지 않고 제약만 교체한다.
alter table public.notice_files
  drop constraint notice_files_sn_uq,
  add constraint notice_files_identity_uq unique (notice_id, file_sn, file_id);
