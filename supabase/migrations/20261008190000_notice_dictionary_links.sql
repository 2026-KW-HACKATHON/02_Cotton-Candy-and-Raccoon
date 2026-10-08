-- Notice-specific positions refer to a saved easy-text snapshot. Complete
-- dictionary results remain in the shared, server-only dictionary cache.
create function public.notice_dictionary_links_valid(candidates jsonb)
returns boolean language plpgsql immutable parallel safe
set search_path = pg_catalog as $$
declare
    item jsonb;
begin
    if jsonb_typeof(candidates) is distinct from 'array' then
        return false;
    end if;
    for item in select value from jsonb_array_elements(candidates)
    loop
        if jsonb_typeof(item) is distinct from 'object'
           or not (item ?& array[
               'original','query_word','context','start','end','cache_key','easy_start',
               'easy_end','easy_expression','mapping_status','lookup_status','error_code','retryable'
           ]) or (select count(*) from jsonb_object_keys(item)) != 13
        then
            return false;
        end if;
        if jsonb_typeof(item -> 'original') is distinct from 'string'
           or jsonb_typeof(item -> 'query_word') is distinct from 'string'
           or jsonb_typeof(item -> 'context') is distinct from 'string'
           or length(item ->> 'original') not between 1 and 100
           or length(item ->> 'query_word') not between 1 and 100
           or length(item ->> 'context') not between 1 and 100000
           or btrim(item ->> 'query_word') = ''
           or jsonb_typeof(item -> 'cache_key') is distinct from 'string'
           or (item ->> 'cache_key') !~ '^[0-9a-f]{64}$'
           or jsonb_typeof(item -> 'start') is distinct from 'number'
           or jsonb_typeof(item -> 'end') is distinct from 'number'
           or (item ->> 'start') !~ '^(0|[1-9][0-9]*)$'
           or (item ->> 'end') !~ '^(0|[1-9][0-9]*)$'
           or (item ->> 'start')::numeric >= (item ->> 'end')::numeric
           or (item ->> 'end')::numeric > 100000
           or (item ->> 'mapping_status') is null
           or (item ->> 'mapping_status') not in ('unchanged','replaced','original_only')
           or (item ->> 'lookup_status') is null
           or (item ->> 'lookup_status') not in ('pending','failed')
           or jsonb_typeof(item -> 'retryable') is distinct from 'boolean'
        then
            return false;
        end if;
        if item ->> 'mapping_status' = 'original_only' then
            if item -> 'easy_start' != 'null'::jsonb or item -> 'easy_end' != 'null'::jsonb
               or item -> 'easy_expression' != 'null'::jsonb then
                return false;
            end if;
        elsif jsonb_typeof(item -> 'easy_start') is distinct from 'number'
           or jsonb_typeof(item -> 'easy_end') is distinct from 'number'
           or (item ->> 'easy_start') !~ '^(0|[1-9][0-9]*)$'
           or (item ->> 'easy_end') !~ '^(0|[1-9][0-9]*)$'
           or (item ->> 'easy_start')::numeric >= (item ->> 'easy_end')::numeric
           or (item ->> 'easy_end')::numeric > 1000000
           or jsonb_typeof(item -> 'easy_expression') is distinct from 'string'
           or length(item ->> 'easy_expression') = 0
        then
            return false;
        end if;
        if item ->> 'lookup_status' = 'pending' then
            if item -> 'error_code' != 'null'::jsonb
               and item ->> 'error_code' is distinct from 'dictionary_lookup_busy' then
                return false;
            end if;
        elsif jsonb_typeof(item -> 'error_code') is distinct from 'string'
           or item ->> 'error_code' not in (
               'dictionary_authentication_failed','dictionary_rate_limited','dictionary_timeout',
               'dictionary_connection_error','dictionary_upstream_error',
               'dictionary_invalid_response','dictionary_result_limit',
               'dictionary_invalid_request','dictionary_missing_api_key','dictionary_lookup_failed',
               'dictionary_lookup_busy','dictionary_storage_error','dictionary_lease_lost',
               'invalid_dictionary_cached_result','dictionary_cache_identity_mismatch',
               'invalid_dictionary_query'
           ) then
            return false;
        end if;
    end loop;
    return true;
end;
$$;

create table public.notice_dictionary_links (
    notice_id bigint primary key references public.notices(id) on delete cascade,
    easy_text_token text not null check (easy_text_token ~ '^[0-9a-f]{64}$'),
    candidates jsonb not null check (public.notice_dictionary_links_valid(candidates)),
    generated_at timestamptz not null default clock_timestamp()
);

alter table public.notice_dictionary_links enable row level security;
revoke all on public.notice_dictionary_links from public, anon, authenticated;
grant select, insert, update, delete on public.notice_dictionary_links to service_role;
create policy "service notice dictionary links" on public.notice_dictionary_links
    for all to service_role using (true) with check (true);

-- Matches storage.notice_easy_text.get_notice_easy_text_cache_token exactly,
-- including generation metadata and dictionary candidates. Timezone independent.
create function public.notice_dictionary_easy_text_token(snapshot public.notice_easy_texts)
returns text language sql immutable parallel safe
set search_path = pg_catalog as $$
    select encode(sha256(convert_to(
        ((to_jsonb(snapshot) - 'generated_at') || jsonb_build_object(
            'generated_at', extract(epoch from snapshot.generated_at)
        ))::text, 'UTF8'
    )), 'hex');
$$;

-- Cache writes normally validate DictionaryResult in Python. The public RPC
-- validates again so an old worker or a damaged nested JSON value cannot be
-- advertised as a successful dictionary lookup merely because result is set.
create function public.notice_dictionary_result_valid(
    result jsonb, expected_query text, expected_contract text
)
returns boolean language plpgsql immutable parallel safe
set search_path = pg_catalog as $$
declare
    entry jsonb;
    sense jsonb;
begin
    if jsonb_typeof(result) is distinct from 'object' then
        return false;
    end if;
    if not (result ?& array['query_word','contract_version','status','entries'])
       or result - array['query_word','contract_version','status','entries'] != '{}'::jsonb
       or jsonb_typeof(result -> 'query_word') is distinct from 'string'
       or jsonb_typeof(result -> 'contract_version') is distinct from 'string'
       or jsonb_typeof(result -> 'status') is distinct from 'string'
       or jsonb_typeof(result -> 'entries') is distinct from 'array'
       or (result ->> 'query_word') is distinct from expected_query
       or (result ->> 'contract_version') is distinct from expected_contract
       or expected_contract is distinct from 'stdict-v1'
       or length(expected_query) not between 1 and 100
       or expected_query is distinct from normalize(expected_query, NFC)
       or expected_query ~ '^[[:space:]]|[[:space:]]$|[[:cntrl:]]'
       or result ->> 'status' not in ('found','not_found')
    then
        return false;
    end if;
    if jsonb_array_length(result -> 'entries') > 1000
       or ((result ->> 'status' = 'found') != (jsonb_array_length(result -> 'entries') > 0))
       or (select count(distinct item ->> 'target_code')
           from jsonb_array_elements(result -> 'entries') item)
          != jsonb_array_length(result -> 'entries')
    then
        return false;
    end if;
    for entry in select value from jsonb_array_elements(result -> 'entries')
    loop
        if jsonb_typeof(entry) is distinct from 'object' then
            return false;
        end if;
        if not (entry ?& array['target_code','headword','source_url','senses'])
           or entry - array['target_code','headword','homonym_number','source_url','senses']
              != '{}'::jsonb
           or jsonb_typeof(entry -> 'target_code') is distinct from 'string'
           or (entry ->> 'target_code') !~ '^[0-9]+$'
           or jsonb_typeof(entry -> 'headword') is distinct from 'string'
           or length(entry ->> 'headword') not between 1 and 200
           or jsonb_typeof(entry -> 'source_url') is distinct from 'string'
           or length(entry ->> 'source_url') > 2048
           or jsonb_typeof(entry -> 'senses') is distinct from 'array'
        then
            return false;
        end if;
        -- DictionaryClient stores this canonical official URL. Restrict public
        -- links to it and the entry's own id, including after out-of-band writes.
        if entry ->> 'source_url' is distinct from
           'https://stdict.korean.go.kr/search/searchView.do?word_no=' || (entry ->> 'target_code')
        then
            return false;
        end if;
        if entry ? 'homonym_number' and entry -> 'homonym_number' != 'null'::jsonb then
            if jsonb_typeof(entry -> 'homonym_number') is distinct from 'string'
               or (entry ->> 'homonym_number') !~ '^[0-9]+$' then
                return false;
            end if;
        end if;
        if jsonb_array_length(entry -> 'senses') not between 1 and 1000
           or (select count(distinct item ->> 'sense_code')
               from jsonb_array_elements(entry -> 'senses') item)
              != jsonb_array_length(entry -> 'senses')
        then
            return false;
        end if;
        for sense in select value from jsonb_array_elements(entry -> 'senses')
        loop
            if jsonb_typeof(sense) is distinct from 'object' then
                return false;
            end if;
            if not (sense ?& array['sense_code','pos_code','part_of_speech','definition'])
               or sense - array['sense_code','pos_code','part_of_speech','definition'] != '{}'::jsonb
               or jsonb_typeof(sense -> 'sense_code') is distinct from 'string'
               or (sense ->> 'sense_code') !~ '^[0-9]+$'
               or jsonb_typeof(sense -> 'pos_code') is distinct from 'string'
               or (sense ->> 'pos_code') !~ '^[0-9]+$'
               or jsonb_typeof(sense -> 'part_of_speech') is distinct from 'string'
               or length(sense ->> 'part_of_speech') not between 1 and 100
               or jsonb_typeof(sense -> 'definition') is distinct from 'string'
               or length(sense ->> 'definition') not between 1 and 20000
            then
                return false;
            end if;
        end loop;
    end loop;
    return true;
end;
$$;

create function public.get_notice_dictionary(notice_id bigint)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog as $$
declare
    snapshot public.notice_easy_texts;
    links public.notice_dictionary_links;
    cache public.standard_dictionary_cache;
    candidate jsonb;
    candidates jsonb := '[]'::jsonb;
    expected_candidates jsonb;
    dictionary jsonb;
    lookup_status text;
    error_code text;
    retryable boolean;
    retry_after_seconds integer;
    dictionary_status text;
begin
    -- SECURITY DEFINER can see backend tables. Apply the full public visibility
    -- rules explicitly before reading links or the common dictionary cache.
    select e.* into snapshot
    from public.notice_easy_texts e join public.notices n on n.id = e.notice_id
    where n.id = get_notice_dictionary.notice_id and n.is_visible
      and public.notice_easy_text_revision(n.title,n.body_html) = e.notice_revision
      and public.notice_easy_text_preserves_title(n.title,e.original_text,e.easy_text,e.changes)
      and public.notice_dictionary_candidates_valid(
          e.original_text,e.dictionary_candidates,char_length(n.title || E'\n')
      );
    if not found then
        return null;
    end if;
    if snapshot.dictionary_candidates is null then
        dictionary_status := 'unprocessed';
        candidates := null;
    else
        select l.* into links from public.notice_dictionary_links l
        where l.notice_id = snapshot.notice_id
          and l.easy_text_token = public.notice_dictionary_easy_text_token(snapshot);
        if found then
            select coalesce(jsonb_agg(jsonb_build_object(
                'original', item -> 'original', 'query_word', item -> 'query_word',
                'context', item -> 'context', 'start', item -> 'start', 'end', item -> 'end'
            ) order by ordinal), '[]'::jsonb) into expected_candidates
            from jsonb_array_elements(links.candidates) with ordinality as rows(item,ordinal);
        end if;
        if links.notice_id is null or expected_candidates is distinct from snapshot.dictionary_candidates
        then
            dictionary_status := 'pending';
            candidates := null;
        else
            dictionary_status := 'complete';
            for candidate in select value from jsonb_array_elements(links.candidates)
            loop
                -- Also check the requested word and contract; an incorrect link
                -- must not expose a different query's cached definitions.
                select c.* into cache from public.standard_dictionary_cache c
                where c.cache_key = candidate ->> 'cache_key'
                  and c.query_word = normalize(btrim(candidate ->> 'query_word'), NFC)
                  and c.contract_version = 'stdict-v1'
                  and c.search_conditions = '{"provider":"stdict","target":1,"method":"exact","pos":0}'::jsonb;
                dictionary := null;
                error_code := null;
                retryable := false;
                retry_after_seconds := 0;
                if cache.result is not null and not public.notice_dictionary_result_valid(
                    cache.result,cache.query_word,cache.contract_version
                ) then
                    lookup_status := 'failed';
                    error_code := 'invalid_dictionary_cached_result';
                elsif cache.result is not null then
                    lookup_status := cache.result ->> 'status';
                    dictionary := jsonb_build_object(
                        'query_word',cache.result -> 'query_word',
                        'contract_version',cache.result -> 'contract_version',
                        'status',cache.result -> 'status','entries',cache.result -> 'entries'
                    );
                elsif cache.lease_expires_at > statement_timestamp() then
                    lookup_status := 'pending';
                    retryable := true;
                    retry_after_seconds := least(3600,greatest(0,ceil(extract(epoch from
                        cache.lease_expires_at - statement_timestamp()))))::integer;
                elsif cache.last_error_code is not null then
                    lookup_status := 'failed';
                    error_code := cache.last_error_code;
                    retryable := error_code in (
                        'dictionary_rate_limited','dictionary_timeout','dictionary_connection_error',
                        'dictionary_upstream_error'
                    );
                    retry_after_seconds := least(3600,greatest(0,ceil(extract(epoch from
                        cache.retry_after_at - statement_timestamp()))))::integer;
                else
                    lookup_status := candidate ->> 'lookup_status';
                    error_code := candidate ->> 'error_code';
                    retryable := (candidate ->> 'retryable')::boolean;
                end if;
                if lookup_status = 'failed' then
                    dictionary_status := 'partial';
                elsif lookup_status = 'pending' and dictionary_status = 'complete' then
                    dictionary_status := 'pending';
                end if;
                candidates := candidates || jsonb_build_array(
                    (candidate - 'cache_key' - 'lookup_status' - 'error_code' - 'retryable') ||
                    jsonb_build_object(
                        'lookup_status',lookup_status,'error_code',error_code,'retryable',retryable,
                        'retry_after_seconds',retry_after_seconds,'dictionary',dictionary
                    )
                );
            end loop;
        end if;
    end if;
    return jsonb_build_object(
        'notice_id',snapshot.notice_id,'notice_revision',snapshot.notice_revision,
        'original_text',snapshot.original_text,'easy_text',snapshot.easy_text,'changes',snapshot.changes,
        'body_text_present',snapshot.body_text_present,
        'attachment_content_included',snapshot.attachment_content_included,
        'model',snapshot.model,'prompt_version',snapshot.prompt_version,
        'generated_at',snapshot.generated_at,'dictionary_status',dictionary_status,
        'dictionary_candidates',candidates
    );
end;
$$;

-- Explicit grants also override Supabase's default grants on new functions.
revoke all on function public.notice_dictionary_links_valid(jsonb)
    from public, anon, authenticated;
revoke all on function public.notice_dictionary_easy_text_token(public.notice_easy_texts)
    from public, anon, authenticated;
revoke all on function public.notice_dictionary_result_valid(jsonb, text, text)
    from public, anon, authenticated;
grant execute on function public.notice_dictionary_links_valid(jsonb),
    public.notice_dictionary_easy_text_token(public.notice_easy_texts),
    public.notice_dictionary_result_valid(jsonb, text, text) to service_role;
revoke all on function public.get_notice_dictionary(bigint) from public, anon, authenticated;
grant execute on function public.get_notice_dictionary(bigint) to anon, authenticated, service_role;

comment on table public.notice_dictionary_links is
    'Server-only positions and safe fallback errors for one exact easy-text generation; no definitions.';
comment on function public.get_notice_dictionary(bigint) is
    'Current visible notice easy text and linked dictionary results. Does not expose cache keys or leases.';
