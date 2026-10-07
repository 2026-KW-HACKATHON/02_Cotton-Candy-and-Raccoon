-- Keep public summaries and in-flight writes bound to the current source version.
-- A counter also distinguishes several content changes in one transaction.
alter table public.notices
  add column content_revision bigint not null default 1
  constraint notices_content_revision_ck check (content_revision > 0);

alter table public.notice_summary_executions add column source_revision bigint;
update public.notice_summary_executions e
  set source_revision = n.content_revision,
      execution_token = nextval('public.notice_summary_execution_token_seq')
  from public.notices n where n.id = e.notice_id;
alter table public.notice_summary_executions alter column source_revision set not null;
alter table public.notice_summary_executions
  add constraint notice_summary_executions_revision_ck check (source_revision > 0);

-- Backend access must also work when service_role has no BYPASSRLS.
-- App grants and policies retain their existing read-only visibility rules.
grant select, insert, update, delete on public.notices, public.notice_files to service_role;
grant usage, select on public.notices_id_seq, public.notice_files_id_seq to service_role;
create policy "service role manages notice sources"
  on public.notices for all to service_role using (true) with check (true);
create policy "service role manages notice source files"
  on public.notice_files for all to service_role using (true) with check (true);

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

revoke all on function public.invalidate_summary_on_source_change() from public;
revoke all on function public.invalidate_summary_on_source_file_change() from public;

comment on column public.notices.content_revision is
  'Monotonic source version. Actual source/attachment metadata changes invalidate public summaries immediately; collection-only updates preserve it.';
comment on column public.notice_summary_executions.source_revision is
  'Private source version captured when the job registers. A guarded result must still match this version and the latest execution token.';
