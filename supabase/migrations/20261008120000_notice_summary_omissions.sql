-- Publish only trusted preparation omissions, atomically with the stored result.
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

alter table public.notice_summaries add column preparation_omissions jsonb
  generated always as (public.summary_preparation_omissions(result, file_manifest)) stored;
grant select (preparation_omissions) on public.notice_summaries to anon, authenticated;
revoke all on function public.summary_preparation_omissions(jsonb, jsonb) from public;
grant execute on function public.summary_preparation_omissions(jsonb, jsonb) to service_role;
comment on column public.notice_summaries.preparation_omissions is
  'Unread file IDs, original links and safe reason codes from trusted preparation. NULL for absent/invalidated results. Preserved together with an existing same-source summary.';
