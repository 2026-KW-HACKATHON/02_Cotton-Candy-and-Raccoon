-- 사전 의미와 검색 결과를 분리한다. 다른 사전·표제어·뜻을 합치지 않는다.
-- 저장된 결과는 수동 갱신할 때까지 재사용한다. 앱에는 읽기만 허용한다.

create table public.glossary_entries (
    provider text not null check (provider in ('onterm', 'opendict', 'krdict')),
    entry_id text not null check (length(btrim(entry_id)) > 0),
    sense_id text not null check (length(btrim(sense_id)) > 0),
    headword text not null check (length(btrim(headword)) > 0),
    definition text check (definition is null or length(btrim(definition)) > 0),
    pos text,
    original_language text,
    easy_terms jsonb not null default '[]'::jsonb
        check (jsonb_typeof(easy_terms) = 'array'),
    norm_info jsonb not null default '[]'::jsonb
        check (jsonb_typeof(norm_info) = 'array'),
    source_url text not null,
    source_name text not null,
    source_institution text,
    source_glossary text,
    entry_id_kind text not null default 'provider_id'
        check (entry_id_kind in ('provider_id', 'content_hash')),
    license text not null,
    license_url text not null,
    queried_at timestamptz not null,
    constraint glossary_entries_provider_contract check (
        (
            provider = 'onterm'
            and entry_id_kind = 'content_hash'
            and entry_id ~ '^sha256:[0-9a-f]{64}$'
            and sense_id = 'content'
            and easy_terms <> '[]'::jsonb
            and source_institution is not null
            and length(btrim(source_institution)) > 0
            and source_glossary is not null
            and length(btrim(source_glossary)) > 0
            and source_name = '국립국어원 온용어'
            and source_url ~ '^https?://kli[.]korean[.]go[.]kr(:80|:443)?([/?#]|$)'
            and license = 'KOGL 1'
            and license_url = 'https://www.kogl.or.kr/info/licenseType1.do'
        )
        or (
            provider in ('opendict', 'krdict')
            and definition is not null
            and entry_id_kind = 'provider_id'
            and license = 'CC BY-SA 2.0 KR'
            and license_url = 'https://creativecommons.org/licenses/by-sa/2.0/kr/'
        )
    ),
    primary key (provider, entry_id, sense_id)
);

create table public.glossary_lookups (
    query text primary key check (length(btrim(query)) between 1 and 200),
    status text not null check (status in ('found', 'not_found')),
    providers_checked text[] not null check (
        array_ndims(providers_checked) = 1
        and array_lower(providers_checked, 1) = 1
        and cardinality(providers_checked) between 1 and 3
        and providers_checked <@ array['onterm', 'opendict', 'krdict']::text[]
        and array_position(providers_checked, null) is null
        and (cardinality(providers_checked) < 2
             or providers_checked[1] <> providers_checked[2])
        and (cardinality(providers_checked) < 3
             or (providers_checked[1] <> providers_checked[3]
                 and providers_checked[2] <> providers_checked[3]))
    ),
    queried_at timestamptz not null
);

create table public.glossary_lookup_entries (
    query text not null references public.glossary_lookups(query) on delete cascade,
    provider text not null,
    entry_id text not null,
    sense_id text not null,
    position integer not null check (position >= 0),
    primary key (query, provider, entry_id, sense_id),
    unique (query, position),
    foreign key (provider, entry_id, sense_id)
        references public.glossary_entries(provider, entry_id, sense_id)
);

comment on table public.glossary_entries is
    '국립국어원 온용어·사전의 의미별 정보. 온용어에는 다듬은 말과 원출처를 보존한다.';
comment on column public.glossary_entries.norm_info is
    '순화 설명의 원문과 적용 조건을 보존한다. 뜻풀이를 쉬운 대체어로 바꾸지 않는다.';
comment on table public.glossary_lookups is
    '정상 조회 결과만 저장한다. API 실패를 not_found로 저장하지 않는다.';

alter table public.glossary_entries enable row level security;
alter table public.glossary_lookups enable row level security;
alter table public.glossary_lookup_entries enable row level security;

revoke all on public.glossary_entries, public.glossary_lookups,
    public.glossary_lookup_entries from public, anon, authenticated;
grant select on public.glossary_entries, public.glossary_lookups,
    public.glossary_lookup_entries to anon, authenticated;

create policy "read glossary meanings" on public.glossary_entries
    for select to anon, authenticated using (true);
create policy "read glossary lookups" on public.glossary_lookups
    for select to anon, authenticated using (true);
create policy "read glossary lookup meanings" on public.glossary_lookup_entries
    for select to anon, authenticated using (true);
