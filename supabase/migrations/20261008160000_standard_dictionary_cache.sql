-- Shared by normalized query, search conditions and response contract, never by notice.
create table public.standard_dictionary_cache (
    cache_key text primary key check (cache_key ~ '^[0-9a-f]{64}$'),
    query_word text not null check (length(query_word) between 1 and 100),
    search_conditions jsonb not null check (jsonb_typeof(search_conditions) = 'object'),
    contract_version text not null check (length(contract_version) between 1 and 100),
    result jsonb,
    result_updated_at timestamptz,
    lease_token uuid,
    lease_expires_at timestamptz,
    retry_after_at timestamptz,
    last_error_code text,
    updated_at timestamptz not null default clock_timestamp(),
    constraint dictionary_cache_identity unique (query_word, search_conditions, contract_version),
    constraint dictionary_cache_result_shape check (
        result is null or (
            jsonb_typeof(result) = 'object'
            and result ?& array['query_word', 'contract_version', 'status', 'entries']
            and result ->> 'query_word' = query_word
            and result ->> 'contract_version' = contract_version
            and result ->> 'status' in ('found', 'not_found')
            and jsonb_typeof(result -> 'entries') = 'array'
            and case when jsonb_typeof(result -> 'entries') = 'array' then
                case when result ->> 'status' = 'found'
                    then jsonb_array_length(result -> 'entries') > 0
                    else jsonb_array_length(result -> 'entries') = 0 end
                else false end
        ) is true
    ),
    constraint dictionary_cache_result_timestamp check (
        (result is null) = (result_updated_at is null)
    ),
    constraint dictionary_cache_lease_pair check (
        (lease_token is null) = (lease_expires_at is null)
    ),
    constraint dictionary_cache_lease_cooldown check (
        lease_token is null or retry_after_at is null
    ),
    constraint dictionary_cache_error_code check (
        last_error_code is null or last_error_code in (
            'dictionary_authentication_failed', 'dictionary_rate_limited', 'dictionary_timeout',
            'dictionary_connection_error', 'dictionary_upstream_error',
            'dictionary_invalid_response', 'dictionary_result_limit',
            'dictionary_invalid_request', 'dictionary_missing_api_key',
            'dictionary_lookup_failed'
        )
    )
);

comment on table public.standard_dictionary_cache is
    'Server-only complete standard dictionary results and short-lived lookup leases. No API keys.';
comment on column public.standard_dictionary_cache.result is
    'found/not_found has no TTL; only an explicit refresh replaces a validated complete result.';
comment on column public.standard_dictionary_cache.lease_expires_at is
    'DB clock deadline for ownership, independent of result lifetime. Expired owners cannot publish.';

alter table public.standard_dictionary_cache enable row level security;
revoke all on public.standard_dictionary_cache from public, anon, authenticated;
grant select, insert, update, delete on public.standard_dictionary_cache to service_role;
create policy "service dictionary cache" on public.standard_dictionary_cache
    for all to service_role using (true) with check (true);
