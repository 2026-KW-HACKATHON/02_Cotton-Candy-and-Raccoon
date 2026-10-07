-- Preserve generated review content for the app with an original-notice warning.
-- Unverified deadlines remain unavailable for sorting. Existing NULL review rows
-- stay valid, including failed regeneration after the source content changed.
alter table public.notice_summaries
  drop constraint notice_summaries_public_result_ck;

-- Require explicit JSON classification fields. SQL UNKNOWN must never allow a
-- missing/null field, string code, decimal code, or mismatched public column.
alter table public.notice_summaries
  add constraint notice_summaries_public_result_ck check (
    case status
      when 'summarized' then
        result is not null and jsonb_typeof(result) = 'object'
        and category is not null and category_code is not null
        and coalesce(jsonb_typeof(result -> 'category') = 'string', false)
        and coalesce(result ->> 'category' = category, false)
        and coalesce(jsonb_typeof(result -> 'category_code') = 'number', false)
        and coalesce(result ->> 'category_code' = category_code::text, false)
      when 'needs_review' then
        deadline_on is null
        and case when result is null then
          category is null and category_code is null
        else
          jsonb_typeof(result) = 'object'
          and coalesce(jsonb_typeof(result -> 'category') = 'string', false)
          and coalesce(result ->> 'category' = coalesce(category, 'unknown'), false)
          and case when category_code is null then
            coalesce(jsonb_typeof(result -> 'category_code') = 'null', false)
          else
            coalesce(jsonb_typeof(result -> 'category_code') = 'number', false)
            and coalesce(result ->> 'category_code' = category_code::text, false)
          end
        end
      when 'pending' then
        result is null and category is null and category_code is null and deadline_on is null
      when 'failed' then
        result is null and category is null and category_code is null and deadline_on is null
      else false
    end
  );

comment on table public.notice_summaries is
  'One current summary per notice. summarized is verified; needs_review may retain generated content with an original-notice warning.';
comment on column public.notice_summaries.result is
  'Validated NoticeSummary and evidence. needs_review content is shown with (원문 확인 요함); NULL remains valid for legacy or changed-source failure rows.';
comment on column public.notice_summaries.category is
  'Notice type, separate from notices.category (source) and category_code (subject). Review JSON unknown maps to SQL NULL.';
comment on column public.notice_summaries.deadline_on is
  'Verified deadline used for sorting. Always NULL for needs_review, pending, and failed.';

-- Existing column grants and RLS continue to expose only visible notices and
-- the eight public columns. No app write or private-metadata grant is added.
