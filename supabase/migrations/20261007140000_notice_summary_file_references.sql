-- Keep trusted preparation provenance private; expose only original file links.
alter table public.notice_summaries add column file_manifest jsonb;
alter table public.notice_summaries
  add constraint notice_summaries_file_manifest_ck check (
    file_manifest is null or (
      jsonb_typeof(file_manifest) = 'object'
      and file_manifest -> 'notice_id' = to_jsonb(notice_id)
      and jsonb_typeof(file_manifest -> 'source_revision') = 'number'
      and jsonb_typeof(file_manifest -> 'original_url') = 'string'
      and jsonb_typeof(file_manifest -> 'files') = 'array'
      and jsonb_typeof(file_manifest -> 'media') = 'array'
    ) is true
  );

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

alter table public.notice_summaries add column file_references jsonb
  generated always as (public.summary_file_references(result, file_manifest)) stored;

revoke all (file_manifest) on public.notice_summaries from public, anon, authenticated;
grant select (file_references) on public.notice_summaries to anon, authenticated;
revoke all on function public.summary_file_references(jsonb, jsonb) from public;
grant execute on function public.summary_file_references(jsonb, jsonb) to service_role;

comment on column public.notice_summaries.file_manifest is
  'Private preparation snapshot validated against the current notice revision and complete file identities. Existing-result correction fallback preserves this snapshot.';
comment on column public.notice_summaries.file_references is
  'Generated public original-file references. File keys, hashes, preparation outcomes and input positions stay private; invalidated or legacy results have no links.';
