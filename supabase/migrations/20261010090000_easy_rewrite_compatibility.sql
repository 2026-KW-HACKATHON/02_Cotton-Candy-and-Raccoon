-- #106: keep a current legacy conversion readable until replacement succeeds.
-- #85's published migration is retained unchanged; only the availability policy changes.
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


comment on column public.app_notice_list.has_easy_text is
 'True for a current readable conversion, including legacy replacements during regeneration.';
