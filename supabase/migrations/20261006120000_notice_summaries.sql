-- #14: verified public summaries, private execution metadata, and content age.
-- Earlier migrations are left intact. Content time is independent of collection time.
alter table public.notices add column content_updated_at timestamptz;
update public.notices set content_updated_at = created_at;
alter table public.notices alter column content_updated_at set default now();
alter table public.notices alter column content_updated_at set not null;

comment on column public.notices.content_updated_at is
  'Last actual notice content/file-list change. Unchanged collection preserves this time.';

create table public.notice_summaries (
  notice_id         bigint primary key,
  status            text not null,
  result            jsonb,
  category          text,
  category_code     integer,
  deadline_on       date,
  attachment_status text not null,
  source_hash       text not null,
  model             text not null,
  prompt_version    text not null,
  attempt_count     integer not null default 0,
  last_error_code   text,
  generated_at      timestamptz,
  updated_at        timestamptz not null default now(),

  constraint notice_summaries_notice_fk foreign key (notice_id)
    references public.notices(id) on delete cascade,
  constraint notice_summaries_status_ck check (
    status in ('pending', 'summarized', 'needs_review', 'failed')
  ),
  constraint notice_summaries_category_ck check (
    category in ('application', 'event', 'living', 'obligation', 'news', 'mixed')
  ),
  constraint notice_summaries_category_code_ck check (
    category_code in (21, 22, 23, 24, 25, 26, 27, 30)
  ),
  constraint notice_summaries_attachment_status_ck check (
    attachment_status in ('none', 'all_read', 'partial', 'unread')
  ),
  constraint notice_summaries_source_hash_ck check (source_hash ~ '^[0-9a-f]{64}$'),
  constraint notice_summaries_model_ck check (model ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$'),
  constraint notice_summaries_prompt_version_ck check (
    prompt_version ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$'
  ),
  constraint notice_summaries_attempt_count_ck check (attempt_count >= 0),
  constraint notice_summaries_error_code_ck check (last_error_code in (
    'input_preparation_failed', 'invalid_prepared_input', 'summary_source_has_no_content',
    'invalid_input', 'empty_input', 'empty_input_block', 'invalid_input_block',
    'unsupported_mime_type', 'unsupported_input_block', 'invalid_media_data',
    'invalid_retry_text', 'input_too_large', 'missing_api_key', 'empty_prompt',
    'api_error', 'api_timeout', 'api_connection_error', 'response_incomplete',
    'empty_response', 'response_validation_failed', 'configuration_error',
    'summary_processing_failed'
  )),
  -- A failed retry may leave a valid existing summarized row with an error code.
  constraint notice_summaries_generated_at_ck check (
    (status in ('pending', 'failed') and generated_at is null)
    or (status in ('summarized', 'needs_review') and generated_at is not null)
  ),
  constraint notice_summaries_failed_error_ck check (
    status <> 'failed' or last_error_code is not null
  ),
  -- JSON null/missing keys must fail rather than pass SQL CHECK as UNKNOWN.
  -- Exact numeric text also rejects a JSON string, decimal, or fractional code.
  constraint notice_summaries_public_result_ck check (
    (
      status = 'summarized'
      and result is not null and jsonb_typeof(result) = 'object'
      and category is not null and category_code is not null
      and coalesce(jsonb_typeof(result -> 'category') = 'string', false)
      and coalesce(result ->> 'category' = category, false)
      and coalesce(jsonb_typeof(result -> 'category_code') = 'number', false)
      and coalesce(result ->> 'category_code' = category_code::text, false)
    )
    or (
      status <> 'summarized'
      and result is null and category is null and category_code is null and deadline_on is null
    )
  )
);

comment on table public.notice_summaries is
  'One current summary per notice. Only summarized contains public verified result JSON.';
comment on column public.notice_summaries.category is
  'Notice type, separate from notices.category (source) and category_code (subject).';
comment on column public.notice_summaries.category_code is
  'Subject: 21 traffic, 22 safety, 23 housing, 24 economy, 25 environment, 26 culture, 27 welfare, 30 administration.';
comment on column public.notice_summaries.result is
  'Validated NoticeSummary and evidence. needs_review means show the original-notice instruction only.';
comment on column public.notice_summaries.source_hash is
  'SHA256 of body plain text and read attachment texts ordered by file_key. Private execution identity.';
comment on column public.notice_summaries.last_error_code is
  'Known safe code only; never raw exception messages, original text, URLs, or credentials.';

-- Include failed retries of existing summaries/review rows, which keep their status.
create index notice_summaries_retry_idx on public.notice_summaries (updated_at, notice_id)
  where status in ('pending', 'failed') or last_error_code is not null;
create index notice_summaries_category_code_idx
  on public.notice_summaries (category_code, notice_id) where status = 'summarized';

alter table public.notice_summaries enable row level security;
create policy "read summaries of visible notices"
  on public.notice_summaries for select to anon, authenticated
  using (exists (
    select 1 from public.notices n where n.id = notice_id and n.is_visible
  ));

-- Revoke default table grants before allowing exactly the app's public columns.
revoke all on public.notice_summaries from public, anon, authenticated;
grant select (notice_id, status, category, category_code, deadline_on, result,
              attachment_status, generated_at)
  on public.notice_summaries to anon, authenticated;

-- Backend-only access is explicit, including environments without BYPASSRLS.
grant all on public.notice_summaries to service_role;
create policy "service role manages summaries"
  on public.notice_summaries for all to service_role using (true) with check (true);
