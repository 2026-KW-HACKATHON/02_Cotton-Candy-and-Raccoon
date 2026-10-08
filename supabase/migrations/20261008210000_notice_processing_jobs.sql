-- Private durable work state. Public summaries/easy text remain in their existing tables.
create table public.notice_processing_jobs (
    notice_id bigint not null references public.notices(id) on delete cascade,
    feature text not null check (feature in ('summary', 'easy_text')),
    input_version text not null,
    contract_key text not null check (length(contract_key) between 1 and 1024),
    state text not null default 'pending'
        check (state in ('pending', 'running', 'retry_wait', 'succeeded',
                         'skipped', 'blocked', 'exhausted')),
    attempts integer not null default 0 check (attempts >= 0),
    next_attempt_at timestamptz,
    last_error_code text check (last_error_code ~ '^[a-z][a-z0-9_]{0,99}$'),
    claim_token uuid,
    lease_expires_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (notice_id, feature),
    constraint notice_processing_jobs_version_ck check (
        (feature = 'summary' and input_version ~ '^[1-9][0-9]{0,18}$')
        or (feature = 'easy_text' and input_version ~ '^[0-9a-f]{64}$')
    ),
    constraint notice_processing_jobs_claim_ck check (
        (state = 'running' and claim_token is not null and lease_expires_at is not null)
        or (state <> 'running' and claim_token is null and lease_expires_at is null)
    ),
    constraint notice_processing_jobs_retry_ck check (
        (state = 'retry_wait' and next_attempt_at is not null)
        or (state <> 'retry_wait' and next_attempt_at is null)
    )
);

create index notice_processing_jobs_ready_idx
    on public.notice_processing_jobs (state, next_attempt_at, updated_at, notice_id)
    where state in ('pending', 'retry_wait', 'running');

alter table public.notice_processing_jobs enable row level security;
revoke all on public.notice_processing_jobs from public, anon, authenticated;
grant select, insert, update, delete on public.notice_processing_jobs to service_role;
create policy "service role manages notice processing jobs"
    on public.notice_processing_jobs for all to service_role
    using (true) with check (true);

comment on table public.notice_processing_jobs is
    'Private per-notice/per-feature retry and lease state. Never replaces public result state.';
comment on column public.notice_processing_jobs.input_version is
    'summary: notices.content_revision as text; easy_text: notice_easy_text_revision(title, body_html).';
comment on column public.notice_processing_jobs.contract_key is
    'Stable compact JSON string containing model and prompt_version. Changing either creates new work.';
comment on column public.notice_processing_jobs.attempts is
    'Claimed executions for this input/contract, including workers lost after claiming; not HTTP requests.';
