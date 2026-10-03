-- #16: Seoul news and source files without Nowon's identifiers.
-- Applied after existing migrations. A future consolidated baseline replaces
-- (never repeats) these changes. Update the BE writer contract before deployment.

alter table public.notices add column source_board text;
update public.notices set source_board = case category
  when 'nowon' then '1001' when 'dong' then '1042' end;

alter table public.notices
  alter column source_board set not null,
  drop constraint notices_post_uq,
  drop constraint notices_category_ck,
  drop constraint notices_shape_ck,
  add constraint notices_post_uq unique (category, source_board, post_sn),
  add constraint notices_category_ck check (category in ('nowon', 'dong', 'seoul')),
  add constraint notices_source_board_ck check (
    (category = 'nowon' and source_board = '1001')
    or (category = 'dong' and source_board = '1042')
    or (category = 'seoul' and source_board in ('21','22','23','24','25','26','27','30'))
  ),
  add constraint notices_shape_ck check (
    (category in ('nowon', 'seoul') and dong_group is null and is_pinned = false)
    or (category = 'dong' and dong_group is not null)
  );

comment on column public.notices.source_board is
  'Source board: Nowon 1001, dong board 1042, SeoulNewsList BLOG_ID. Not dong_group.';
comment on column public.notices.post_sn is
  'Source post ID as text: Nowon ID, dong q_bbscttSn, Seoul POST_ID. Preserve leading zeros.';

alter table public.notice_files
  add column file_key text,
  alter column file_sn drop not null,
  alter column file_id drop not null;
update public.notice_files set file_key = 'id:' || file_id;

alter table public.notice_files
  alter column file_key set not null,
  drop constraint notice_files_identity_uq,
  add constraint notice_files_identity_uq unique (notice_id, file_key, kind),
  add constraint notice_files_identifiers_ck check (
    (file_sn is null or (file_sn = btrim(file_sn) and length(file_sn) > 0))
    and (file_id is null or (file_id = btrim(file_id) and length(file_id) > 0))
  ),
  add constraint notice_files_url_nonblank_ck check (url = btrim(url) and length(url) > 0),
  add constraint notice_files_key_ck check (
    file_key = case when file_id is not null then 'id:' || file_id
      else 'url:' || encode(sha256(convert_to(url, 'UTF8')), 'hex') end
  );

-- Writers normalize URLs before storage. This key identifies a reference,
-- not identical contents or a permanent source UUID.
comment on column public.notice_files.file_key is
  'id:<actual file_id>, or url:<SHA256 hex of stored normalized URL UTF-8> if file_id is NULL.';
comment on column public.notice_files.file_id is
  'Actual source file ID; NULL when absent. Do not invent a UUID.';
comment on column public.notice_files.file_sn is
  'Actual source file group/serial number; NULL when absent, not a uniqueness key.';
-- Existing RLS and grants stay unchanged. file_key is not granted to app roles.
