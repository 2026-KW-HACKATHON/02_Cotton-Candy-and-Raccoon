-- Register execution order before Gemini starts without clearing public content.
create sequence public.notice_summary_execution_token_seq as bigint;

create table public.notice_summary_executions (
  notice_id bigint primary key references public.notices(id) on delete cascade,
  execution_token bigint not null default nextval('public.notice_summary_execution_token_seq'),
  constraint notice_summary_executions_token_ck check (execution_token > 0)
);

comment on table public.notice_summary_executions is
  'Private latest registered execution per notice. Matching tokens guard result writes; never an app result or source hash.';
comment on column public.notice_summary_executions.execution_token is
  'Order registered before Gemini. Concurrent UPDATE allocates its new token after acquiring the registry row lock.';

alter table public.notice_summary_executions enable row level security;
revoke all on public.notice_summary_executions from public, anon, authenticated;
revoke all on public.notice_summary_execution_token_seq from public, anon, authenticated;
grant all on public.notice_summary_executions to service_role;
grant usage, select on public.notice_summary_execution_token_seq to service_role;
create policy "service role manages summary executions"
  on public.notice_summary_executions for all to service_role
  using (true) with check (true);
