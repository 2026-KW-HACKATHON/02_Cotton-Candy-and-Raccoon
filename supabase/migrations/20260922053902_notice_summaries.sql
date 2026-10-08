-- ============================================================================
-- notice_summaries: 요약 결과, 요약 실행, 원문 변경 trigger
--
-- notices migration 다음에 적용한다. 원문 변경 trigger는 notices.content_revision을
-- 올리면서 notice_summaries의 공개 결과를 비우므로 이 파일에 둔다.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- notice_summaries
--
-- 생성 컬럼(file_references, preparation_omissions)이 호출하는 함수를 먼저 만든다.
-- ----------------------------------------------------------------------------

create function public.summary_file_references(summary_result jsonb, manifest jsonb)
returns jsonb language sql immutable strict parallel safe
set search_path = pg_catalog as $$
  select case when summary_result is null then null else coalesce(jsonb_agg(
    jsonb_build_object(
      'source_id', media.value -> 'source_id',
      'source_type', media.value -> 'source_type',
      'files', aliases.files,
      'original_notice_url', case when jsonb_array_length(aliases.files) = 0
        then manifest -> 'original_url' else null end,
      'guidance', case when jsonb_array_length(aliases.files) = 0
        then '원문에서 확인' else null end
    ) order by media.ordinality
  ), '[]'::jsonb) end
  from jsonb_array_elements(manifest -> 'media') with ordinality as media(value, ordinality)
  cross join lateral (
    select coalesce(jsonb_agg(jsonb_build_object(
      'notice_file_id', source_file.value -> 'notice_file_id',
      'kind', source_file.value -> 'kind',
      'url', source_file.value -> 'url'
    ) order by source_file.ordinality), '[]'::jsonb) as files
    from jsonb_array_elements(manifest -> 'files') with ordinality
      as source_file(value, ordinality)
    where source_file.value ->> 'source_id' = media.value ->> 'source_id'
      and source_file.value ->> 'outcome' = 'media'
  ) as aliases
$$;


create function public.summary_preparation_omissions(summary_result jsonb, manifest jsonb)
returns jsonb language sql immutable strict parallel safe
set search_path = pg_catalog as $$
  select coalesce(jsonb_agg(jsonb_build_object(
    'notice_file_id', omission.value -> 'notice_file_id',
    'url', omission.value -> 'url',
    'reason_code', omission.value -> 'reason_code'
  ) order by omission.ordinality), '[]'::jsonb)
  from jsonb_array_elements(coalesce(manifest -> 'omissions', '[]'::jsonb))
    with ordinality as omission(value, ordinality)
$$;


-- 같은 원문의 재요약 후보가 기존 정보를 잃는지 비교한다. summary 저장 UPSERT가 행 잠금 안에서 호출한다.
-- 문장이 같은지가 아니라 정보 범위를 비교한다. 비어 있지 않은 값의 정정은 허용하고 자동 삭제는 막는다.
create function public.summary_information_loss(
  previous jsonb, candidate jsonb, previous_deadline date, candidate_deadline date
) returns boolean
language plpgsql immutable security invoker set search_path = pg_catalog as $$
declare
  field_name text;
  old_value text;
  new_value text;
  old_dates jsonb;
  new_dates jsonb;
  old_items jsonb;
  new_items jsonb;
begin
  if previous is null or previous = 'null'::jsonb then
    return false;
  end if;
  if previous_deadline is not null and candidate_deadline is null then
    return true;
  end if;

  foreach field_name in array array[
    'summary', 'audience', 'action', 'location', 'publisher', 'applicable_area',
    'category', 'category_code', 'audience_scope', 'action_requirement'
  ] loop
    old_value := btrim(previous ->> field_name);
    new_value := btrim(candidate ->> field_name);
    if coalesce(old_value, '') not in ('', 'unknown')
       and coalesce(new_value, '') in ('', 'unknown') then
      return true;
    end if;
  end loop;

  foreach field_name in array array['audience', 'deadline', 'action', 'notes'] loop
    old_value := btrim(previous -> 'card_summaries' ->> field_name);
    new_value := btrim(candidate -> 'card_summaries' ->> field_name);
    if coalesce(old_value, '') <> '' and coalesce(new_value, '') = '' then
      return true;
    end if;
  end loop;

  -- Missing and JSON-null collections in legacy summaries carry no coverage.
  old_dates := case when jsonb_typeof(previous -> 'dates') = 'array'
    then previous -> 'dates' else '[]'::jsonb end;
  new_dates := case when jsonb_typeof(candidate -> 'dates') = 'array'
    then candidate -> 'dates' else '[]'::jsonb end;
  if exists (
    with old_coverage as (
      select item ->> 'kind' as kind, count(*) as entries,
        count(item ->> 'start_date') as start_dates,
        count(item ->> 'end_date') as end_dates,
        count(item ->> 'start_time') as start_times,
        count(item ->> 'end_time') as end_times
      from jsonb_array_elements(old_dates) as item group by item ->> 'kind'
    ), new_coverage as (
      select item ->> 'kind' as kind, count(*) as entries,
        count(item ->> 'start_date') as start_dates,
        count(item ->> 'end_date') as end_dates,
        count(item ->> 'start_time') as start_times,
        count(item ->> 'end_time') as end_times
      from jsonb_array_elements(new_dates) as item group by item ->> 'kind'
    )
    select 1 from old_coverage o left join new_coverage n
      on o.kind is not distinct from n.kind
    where o.entries > coalesce(n.entries, 0)
      or o.start_dates > coalesce(n.start_dates, 0)
      or o.end_dates > coalesce(n.end_dates, 0)
      or o.start_times > coalesce(n.start_times, 0)
      or o.end_times > coalesce(n.end_times, 0)
  ) then
    return true;
  end if;

  foreach field_name in array array['notes', 'topics'] loop
    old_items := case when jsonb_typeof(previous -> field_name) = 'array'
      then previous -> field_name else '[]'::jsonb end;
    new_items := case when jsonb_typeof(candidate -> field_name) = 'array'
      then candidate -> field_name else '[]'::jsonb end;
    if jsonb_array_length(old_items) > jsonb_array_length(new_items) then
      return true;
    end if;
  end loop;

  old_items := case when jsonb_typeof(previous -> 'evidence') = 'array'
    then previous -> 'evidence' else '[]'::jsonb end;
  new_items := case when jsonb_typeof(candidate -> 'evidence') = 'array'
    then candidate -> 'evidence' else '[]'::jsonb end;
  -- Resolving a review uncertainty improves a candidate; its explanatory
  -- citation is not a source fact that must survive the completed result.
  if exists (
    select item ->> 'field' from jsonb_array_elements(old_items) as item
      where nullif(btrim(item ->> 'field'), '') is not null
        and item ->> 'field' <> 'uncertainties'
    except
    select item ->> 'field' from jsonb_array_elements(new_items) as item
  ) then
    return true;
  end if;
  return false;
end
$$;


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

  -- result.card_summaries를 그대로 꺼낸 앱 카드 4종. JSON null과 누락은 SQL NULL.
  card_summaries    jsonb generated always as (nullif(result -> 'card_summaries', 'null'::jsonb)) stored,

  -- 입력 준비 스냅샷(파일별 처리 결과, 입력 위치, 해시). 앱에 공개하지 않는다.
  file_manifest     jsonb,

  -- 앱에 공개하는 원본 파일 링크와 읽지 못한 파일 안내. file_manifest에서 생성한다.
  file_references   jsonb generated always as (public.summary_file_references(result, file_manifest)) stored,
  preparation_omissions jsonb
    generated always as (public.summary_preparation_omissions(result, file_manifest)) stored,

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
    'summary_processing_failed', 'summary_information_loss'
  )),
  -- A failed retry may leave a valid existing summarized row with an error code.
  constraint notice_summaries_generated_at_ck check (
    (status in ('pending', 'failed') and generated_at is null)
    or (status in ('summarized', 'needs_review') and generated_at is not null)
  ),
  constraint notice_summaries_failed_error_ck check (
    status <> 'failed' or last_error_code is not null
  ),
  -- summarized: 검증된 공개 result와 분류값이 반드시 있다. JSON null이나 누락 키는
  -- SQL CHECK에서 UNKNOWN으로 통과하지 않게 coalesce로 막는다.
  -- needs_review: 생성된 내용을 원문 확인 안내와 함께 보일 수 있다. 마감일은 정렬에 쓰지 않는다.
  -- pending, failed: 공개 결과가 없다.
  constraint notice_summaries_public_result_ck check (
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
  ),
  constraint notice_summaries_card_summaries_ck check (
    case
      when card_summaries is null then true
      when jsonb_typeof(card_summaries) = 'object' then
        card_summaries ?& array['audience', 'deadline', 'action', 'notes']
        and card_summaries - array['audience', 'deadline', 'action', 'notes'] = '{}'::jsonb
        and coalesce(
          jsonb_typeof(card_summaries -> 'audience') = 'null'
          or (
            jsonb_typeof(card_summaries -> 'audience') = 'string'
            and card_summaries ->> 'audience' !~ U&'^[[:space:]\001C\001D\001E\001F\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000]*$'
            and card_summaries ->> 'audience' !~ '[\r\n]'
          ), false
        )
        and coalesce(
          jsonb_typeof(card_summaries -> 'deadline') = 'null'
          or (
            jsonb_typeof(card_summaries -> 'deadline') = 'string'
            and card_summaries ->> 'deadline' !~ U&'^[[:space:]\001C\001D\001E\001F\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000]*$'
            and card_summaries ->> 'deadline' !~ '[\r\n]'
          ), false
        )
        and coalesce(
          jsonb_typeof(card_summaries -> 'action') = 'null'
          or (
            jsonb_typeof(card_summaries -> 'action') = 'string'
            and card_summaries ->> 'action' !~ U&'^[[:space:]\001C\001D\001E\001F\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000]*$'
            and card_summaries ->> 'action' !~ '[\r\n]'
          ), false
        )
        and coalesce(
          jsonb_typeof(card_summaries -> 'notes') = 'null'
          or (
            jsonb_typeof(card_summaries -> 'notes') = 'string'
            and card_summaries ->> 'notes' !~ U&'^[[:space:]\001C\001D\001E\001F\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000]*$'
            and card_summaries ->> 'notes' !~ '[\r\n]'
          ), false
        )
      else false
    end
  ),
  constraint notice_summaries_file_manifest_ck check (
    file_manifest is null or (
      jsonb_typeof(file_manifest) = 'object'
      and file_manifest -> 'notice_id' = to_jsonb(notice_id)
      and jsonb_typeof(file_manifest -> 'source_revision') = 'number'
      and jsonb_typeof(file_manifest -> 'original_url') = 'string'
      and jsonb_typeof(file_manifest -> 'files') = 'array'
      and jsonb_typeof(file_manifest -> 'media') = 'array'
    ) is true
  )
);

-- Include failed retries of existing summaries/review rows, which keep their status.
create index notice_summaries_retry_idx on public.notice_summaries (updated_at, notice_id)
  where status in ('pending', 'failed') or last_error_code is not null;
create index notice_summaries_category_code_idx
  on public.notice_summaries (category_code, notice_id) where status = 'summarized';


-- ----------------------------------------------------------------------------
-- notice_summary_executions
--
-- Gemini 호출 전에 실행 순서와 원문 버전을 등록한다. 결과는 최신 토큰이고
-- 원문 버전이 그대로일 때만 저장된다. 앱에 공개하지 않는다.
-- ----------------------------------------------------------------------------
create sequence public.notice_summary_execution_token_seq as bigint;

create table public.notice_summary_executions (
  notice_id bigint primary key references public.notices(id) on delete cascade,
  execution_token bigint not null default nextval('public.notice_summary_execution_token_seq'),
  constraint notice_summary_executions_token_ck check (execution_token > 0),
  source_revision bigint not null,
  constraint notice_summary_executions_revision_ck check (source_revision > 0)
);


-- ----------------------------------------------------------------------------
-- 원문 변경 trigger
--
-- 원문이나 파일 목록이 바뀌면 content_revision을 올리고 공개 요약을 즉시 비운다.
-- 수집 시각, 공개 여부처럼 원문이 아닌 값만 바뀌면 버전을 유지한다.
-- 같은 transaction 안에서 여러 번 바뀌어도 버전 숫자로 구분된다.
-- ----------------------------------------------------------------------------
create function public.invalidate_summary_on_source_change() returns trigger
language plpgsql set search_path = pg_catalog, public as $$
declare
  source_changed boolean;
begin
  source_changed := row(new.category, new.source_board, new.dong_group, new.post_sn,
         new.title, new.department, new.registered_on, new.url, new.body_html, new.license_type)
       is distinct from
     row(old.category, old.source_board, old.dong_group, old.post_sn,
         old.title, old.department, old.registered_on, old.url, old.body_html, old.license_type);
  if source_changed or new.content_revision is distinct from old.content_revision then
    new.content_revision := old.content_revision + 1;
    new.content_updated_at := now();
    if source_changed then new.is_modified := true; end if;
    update public.notice_summaries set
      status = case when status = 'summarized' then 'needs_review' else status end,
      result = null, category = null, category_code = null, deadline_on = null,
      updated_at = now()
      where notice_id = old.id
        and (result is not null or category is not null or category_code is not null
             or deadline_on is not null);
  else
    -- Collection timestamps, visibility, and other unchanged-source metadata
    -- cannot reset or arbitrarily replace the version used by a summary job.
    new.content_revision := old.content_revision;
  end if;
  return new;
end
$$;

create trigger notices_summary_source_change
  before update on public.notices
  for each row execute function public.invalidate_summary_on_source_change();

create function public.invalidate_summary_on_source_file_change() returns trigger
language plpgsql set search_path = pg_catalog, public as $$
declare
  previous_notice_id bigint;
  current_notice_id bigint;
  parent_notice_id bigint;
begin
  if tg_op = 'UPDATE' and row(new.notice_id, new.kind, new.file_sn, new.file_id,
                              new.file_key, new.file_name, new.url)
       is not distinct from row(old.notice_id, old.kind, old.file_sn, old.file_id,
                                old.file_key, old.file_name, old.url) then
    return null;
  end if;
  if tg_op <> 'INSERT' then previous_notice_id := old.notice_id; end if;
  if tg_op <> 'DELETE' then current_notice_id := new.notice_id; end if;
  -- Consistent parent order also covers moving a file between two notices.
  for parent_notice_id in
    select id from public.notices where id in (previous_notice_id, current_notice_id)
      order by id for no key update
  loop
    update public.notices set content_revision = content_revision + 1
      where id = parent_notice_id;
  end loop;
  return null;
end
$$;

create trigger notice_files_summary_source_change
  after insert or update or delete on public.notice_files
  for each row execute function public.invalidate_summary_on_source_file_change();


-- ============================================================================
-- 앱 읽기 권한과 backend 권한
-- ============================================================================

-- notice_summaries: 보이는 공지의 요약만, 앱이 쓰는 컬럼만 읽는다.
alter table public.notice_summaries enable row level security;
create policy "read summaries of visible notices"
  on public.notice_summaries for select to anon, authenticated
  using (exists (
    select 1 from public.notices n where n.id = notice_id and n.is_visible
  ));


-- Revoke default table grants before allowing exactly the app's public columns.
revoke all on public.notice_summaries from public, anon, authenticated;
grant select (notice_id, status, category, category_code, deadline_on, result,
              attachment_status, generated_at, card_summaries, file_references,
              preparation_omissions)
  on public.notice_summaries to anon, authenticated;


-- Backend-only access is explicit, including environments without BYPASSRLS.
grant all on public.notice_summaries to service_role;
create policy "service role manages summaries"
  on public.notice_summaries for all to service_role using (true) with check (true);

-- notice_summary_executions: backend 전용.
alter table public.notice_summary_executions enable row level security;
revoke all on public.notice_summary_executions from public, anon, authenticated;
revoke all on public.notice_summary_execution_token_seq from public, anon, authenticated;
grant all on public.notice_summary_executions to service_role;
grant usage, select on public.notice_summary_execution_token_seq to service_role;
create policy "service role manages summary executions"
  on public.notice_summary_executions for all to service_role
  using (true) with check (true);

-- 함수 실행 권한: trigger와 생성 컬럼 함수는 backend만 호출한다.
-- Supabase는 새 함수에 anon, authenticated 실행 권한을 따로 주므로 public과 함께 회수한다.
-- 앱이 읽는 생성 컬럼은 저장된 값이라 읽을 때 함수를 실행하지 않는다.
revoke all on function public.invalidate_summary_on_source_change()
  from public, anon, authenticated;
revoke all on function public.invalidate_summary_on_source_file_change()
  from public, anon, authenticated;
revoke all on function public.summary_file_references(jsonb, jsonb)
  from public, anon, authenticated;
grant execute on function public.summary_file_references(jsonb, jsonb) to service_role;
revoke all on function public.summary_preparation_omissions(jsonb, jsonb)
  from public, anon, authenticated;
grant execute on function public.summary_preparation_omissions(jsonb, jsonb) to service_role;
revoke all on function public.summary_information_loss(jsonb, jsonb, date, date)
  from public, anon, authenticated;
grant execute on function public.summary_information_loss(jsonb, jsonb, date, date)
  to service_role;


-- ============================================================================
-- 설명(comment)
-- ============================================================================
comment on table public.notice_summaries is
  'One current summary per notice. summarized is verified; needs_review may retain generated content with an original-notice warning.';
comment on column public.notice_summaries.category is
  'Notice type, separate from notices.category (source) and category_code (subject). Review JSON unknown maps to SQL NULL.';
comment on column public.notice_summaries.category_code is
  'Subject: 21 traffic, 22 safety, 23 housing, 24 economy, 25 environment, 26 culture, 27 welfare, 30 administration.';
comment on column public.notice_summaries.result is
  'Validated NoticeSummary and evidence. needs_review content is shown with (원문 확인 요함); NULL remains valid for legacy or changed-source failure rows.';
comment on column public.notice_summaries.source_hash is
  'SHA256 of body plain text and read attachment texts ordered by file_key. Private execution identity.';
comment on column public.notice_summaries.last_error_code is
  'Private safe execution code. summary_information_loss means a candidate was rejected while retaining public content; it is not a transient provider failure or an automatic retry instruction.';
comment on column public.notice_summaries.deadline_on is
  'Verified deadline used for sorting. Always NULL for needs_review, pending, and failed.';
comment on column public.notice_summaries.card_summaries is
  'Generated from result.card_summaries: audience/deadline/action/notes, each string or JSON null. Legacy missing/null cards become SQL NULL; needs_review keeps the original-notice warning.';
comment on table public.notice_summary_executions is
  'Private latest registered execution per notice. Matching tokens guard result writes; never an app result or source hash.';
comment on column public.notice_summary_executions.execution_token is
  'Order registered before Gemini. Concurrent UPDATE allocates its new token after acquiring the registry row lock.';
comment on column public.notice_summary_executions.source_revision is
  'Private source version captured when the job registers. A guarded result must still match this version and the latest execution token.';
comment on column public.notice_summaries.file_manifest is
  'Private preparation snapshot validated against the current notice revision and complete file identities. Existing-result correction fallback preserves this snapshot.';
comment on column public.notice_summaries.file_references is
  'Generated public original-file references. File keys, hashes, preparation outcomes and input positions stay private; invalidated or legacy results have no links.';
comment on column public.notice_summaries.preparation_omissions is
  'Unread file IDs, original links and safe reason codes from trusted preparation. NULL for absent/invalidated results. Preserved together with an existing same-source summary.';
comment on function public.summary_information_loss(jsonb, jsonb, date, date) is
  'Private coverage comparison used atomically by summary writers after source/execution validation. Missing facts, cards, dates, evidence fields, or a sorting deadline cannot silently replace existing information.';
