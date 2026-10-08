-- Reject information loss inside the summary UPSERT, under its row lock.
-- This compares information coverage, not prose equality: corrected nonempty
-- values and dates can replace old values, while automatic removal cannot.
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

revoke all on function public.summary_information_loss(jsonb, jsonb, date, date)
  from public, anon, authenticated;
grant execute on function public.summary_information_loss(jsonb, jsonb, date, date)
  to service_role;
comment on function public.summary_information_loss(jsonb, jsonb, date, date) is
  'Private coverage comparison used atomically by summary writers after source/execution validation. Missing facts, cards, dates, evidence fields, or a sorting deadline cannot silently replace existing information.';

alter table public.notice_summaries
  drop constraint notice_summaries_error_code_ck,
  add constraint notice_summaries_error_code_ck check (last_error_code in (
    'input_preparation_failed', 'invalid_prepared_input', 'summary_source_has_no_content',
    'invalid_input', 'empty_input', 'empty_input_block', 'invalid_input_block',
    'unsupported_mime_type', 'unsupported_input_block', 'invalid_media_data',
    'invalid_retry_text', 'input_too_large', 'missing_api_key', 'empty_prompt',
    'api_error', 'api_timeout', 'api_connection_error', 'response_incomplete',
    'empty_response', 'response_validation_failed', 'configuration_error',
    'summary_processing_failed', 'summary_information_loss'
  ));

comment on column public.notice_summaries.last_error_code is
  'Private safe execution code. summary_information_loss means a candidate was rejected while retaining public content; it is not a transient provider failure or an automatic retry instruction.';
