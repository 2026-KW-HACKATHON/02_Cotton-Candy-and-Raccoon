-- Backend integration of #58. Its original version collides with #53 on backend.
-- Idempotent when #58 was already applied on develop; preserve the public contract.
-- ============================================================================
-- 앱 공개 조회 계약: 본문 평문, 쉬운말 공개 컬럼, 앱 전용 view 2개 (#58)
--
-- 앱(Expo)은 테이블이 아니라 app_notice_list(목록, 홈, 보관함)와
-- app_notice_detail(상세)만 읽는다. view가 앱과 DB 사이의 계약이다.
-- 컬럼 추가는 하위 호환으로 허용하고, 이름 변경과 삭제는 FE와 합의한 뒤
-- 새 migration으로 한다.
--
-- view는 security_invoker = true로 만든다. 이 옵션이 없으면 view가 소유자
-- 권한으로 실행되어 RLS를 건너뛰고 숨긴 공지가 앱에 보인다. 이 옵션 덕분에
-- 아래 테이블의 RLS(보이는 공지만, 현재 원문 버전의 쉬운말만)와 컬럼 grant가
-- 요청한 역할 기준으로 그대로 적용된다.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- notices.body_text: 앱이 표시하는 본문 평문
--
-- React Native는 HTML을 바로 그리지 못하므로 pipeline이 저장할 때
-- html_to_notice_text(body_html)로 만든다. 요약 입력의 본문 평문과 같은 함수다.
-- body_html에서 파생한 값이라 원문 변경 trigger의 비교 대상에 넣지 않는다.
-- 평문이 비면 null이다.
-- ----------------------------------------------------------------------------
alter table public.notices add column if not exists body_text text;

comment on column public.notices.body_text is
  'Plain text derived from body_html by the pipeline (html_to_notice_text) on every save; NULL when empty. Not a source field for revision tracking.';


-- ----------------------------------------------------------------------------
-- notice_easy_texts: 앱 공개 컬럼을 좁힌다
--
-- 테이블 단위 select를 회수하고 화면에 필요한 컬럼만 다시 준다.
-- notice_revision, source_hash, model, prompt_version, attempt_count는 비공개다.
-- 읽기 policy는 notice_revision을 참조하지만, policy 식은 컬럼 grant 검사를
-- 받지 않으므로 그대로 동작한다. policy가 호출하는 함수 2개의 실행 권한은 유지한다.
-- ----------------------------------------------------------------------------
revoke select on public.notice_easy_texts from anon, authenticated;
grant select (notice_id, original_text, easy_text, changes, body_text_present,
              attachment_content_included, generated_at)
  on public.notice_easy_texts to anon, authenticated;


-- ----------------------------------------------------------------------------
-- app_notice_list: 목록, 홈, 보관함
--
-- 요약과 쉬운말이 없는 공지도 left join으로 남긴다.
-- 다른 동 글(dong_group = 'other')은 고정 해제 글 숨김 처리 전까지 제외한다.
--
-- display_status는 Python build_notice_summary_view와 같은 규칙이다.
--   요약 행 없음                                → none
--   summarized이고 preparation_omissions가 있음 → needs_review
--   그 외                                       → status 그대로
-- pipeline 저장 코드는 검토가 필요한 결과를 summarized로 저장하지 않으므로,
-- pipeline이 쓴 행에서는 이 규칙과 Python 결과가 같다.
--
-- has_easy_text는 쉬운말 RLS가 걸러 낸 뒤의 결과다. 원문이 바뀌어 버전이 맞지
-- 않거나 제목을 보존하지 않은 결과는 false다.
-- ----------------------------------------------------------------------------
create or replace view public.app_notice_list with (security_invoker = true) as
select
  n.id,
  n.category as source,
  n.dong_group,
  n.is_pinned,
  n.title,
  n.department,
  n.registered_on,
  n.content_updated_at,
  n.is_modified,
  s.status as summary_status,
  case
    when s.notice_id is null then 'none'
    when s.status = 'summarized'
      and jsonb_array_length(coalesce(s.preparation_omissions, '[]'::jsonb)) > 0
      then 'needs_review'
    else s.status
  end as display_status,
  s.category as notice_type,
  s.category_code,
  s.deadline_on,
  s.result ->> 'summary' as headline,
  s.card_summaries,
  s.attachment_status,
  exists (
    select 1 from public.notice_easy_texts e where e.notice_id = n.id
  ) as has_easy_text
from public.notices n
left join public.notice_summaries s on s.notice_id = n.id
where n.is_visible
  and n.dong_group is distinct from 'other';


-- ----------------------------------------------------------------------------
-- app_notice_detail: 상세
--
-- app_notice_list의 모든 컬럼에 원문, 요약 전체, 파일, 쉬운말을 더한다.
-- 한 SQL 문이므로 공지, 요약, 쉬운말이 같은 시점의 값으로 나온다.
-- files는 원본 첨부와 본문 이미지 목록이다. 요약 근거 목록(file_references)과 별개다.
-- ----------------------------------------------------------------------------
create or replace view public.app_notice_detail with (security_invoker = true) as
select
  l.id,
  l.source,
  l.dong_group,
  l.is_pinned,
  l.title,
  l.department,
  l.registered_on,
  l.content_updated_at,
  l.is_modified,
  l.summary_status,
  l.display_status,
  l.notice_type,
  l.category_code,
  l.deadline_on,
  l.headline,
  l.card_summaries,
  l.attachment_status,
  l.has_easy_text,
  n.url,
  n.license_type,
  n.body_text,
  s.result,
  s.generated_at,
  s.file_references,
  s.preparation_omissions,
  coalesce((
    select jsonb_agg(jsonb_build_object('id', f.id, 'kind', f.kind, 'url', f.url) order by f.id)
    from public.notice_files f
    where f.notice_id = n.id
  ), '[]'::jsonb) as files,
  e.original_text as easy_original_text,
  e.easy_text,
  e.changes as easy_changes,
  e.body_text_present as easy_body_text_present,
  e.attachment_content_included as easy_attachment_content_included,
  e.generated_at as easy_generated_at
from public.app_notice_list l
join public.notices n on n.id = l.id
left join public.notice_summaries s on s.notice_id = n.id
left join public.notice_easy_texts e on e.notice_id = n.id;


-- ----------------------------------------------------------------------------
-- 권한: 앱 역할은 두 view를 읽기만 한다
--
-- Supabase는 새 view에도 anon, authenticated의 모든 권한을 자동으로 주므로
-- 먼저 회수하고 select만 다시 준다.
-- ----------------------------------------------------------------------------
revoke all on public.app_notice_list, public.app_notice_detail
  from public, anon, authenticated;
grant select on public.app_notice_list, public.app_notice_detail to anon, authenticated;


-- ============================================================================
-- 설명(comment)
-- ============================================================================
comment on view public.app_notice_list is
  'App read contract for lists, home and saved notices. security_invoker: table RLS and column grants apply to the caller. Excludes other-dong posts until unpinned-post hiding exists.';
comment on view public.app_notice_detail is
  'App read contract for one notice: list columns plus original text, full summary, files and current easy text. security_invoker.';
comment on column public.app_notice_list.source is
  'Collection source (notices.category): nowon, dong or seoul. Not the subject category.';
comment on column public.app_notice_list.display_status is
  'Screen state: none when no summary row, needs_review for summarized rows with preparation omissions, otherwise the stored status. Matches build_notice_summary_view.';
comment on column public.app_notice_list.notice_type is
  'Notice type (notice_summaries.category): application, event, living, obligation, news, mixed, or NULL.';
comment on column public.app_notice_list.category_code is
  'Subject code 21-27 or 30. NULL means unclassified, including notices without a summary.';
comment on column public.app_notice_list.headline is
  'One-line summary (result.summary). NULL when no public summary exists; never the original title.';
comment on column public.app_notice_list.has_easy_text is
  'True when the caller can read a current easy-text row (same revision, title preserved).';
comment on column public.app_notice_detail.files is
  'Original attachments and body images as [{id, kind, url}] ordered by id. File names stay private.';
comment on column public.app_notice_detail.easy_changes is
  'Easy-text replacements; start/end are Python code point offsets in easy_original_text, not UTF-16.';
