-- 표준국어대사전을 먼저 조회하고, 결과가 없을 때 우리말샘을 조회한다.
-- 기존 사전 기록·의미 식별자·온용어 이용 조건·읽기 정책을 보존한다.
alter table public.glossary_entries
    drop constraint glossary_entries_provider_check,
    drop constraint glossary_entries_provider_contract,
    add constraint glossary_entries_provider_check
        check (provider in ('stdict', 'onterm', 'opendict', 'krdict')),
    add constraint glossary_entries_provider_contract check (
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
            provider in ('stdict', 'opendict', 'krdict')
            and definition is not null
            and entry_id_kind = 'provider_id'
            and license = 'CC BY-SA 2.0 KR'
            and license_url = 'https://creativecommons.org/licenses/by-sa/2.0/kr/'
        )
    );

alter table public.glossary_lookups
    drop constraint glossary_lookups_providers_checked_check,
    add constraint glossary_lookups_providers_checked_check check (
        array_ndims(providers_checked) = 1
        and array_lower(providers_checked, 1) = 1
        and cardinality(providers_checked) between 1 and 4
        and providers_checked <@ array['stdict', 'onterm', 'opendict', 'krdict']::text[]
        and array_position(providers_checked, null) is null
        and (cardinality(providers_checked) < 2
             or providers_checked[1] <> providers_checked[2])
        and (cardinality(providers_checked) < 3
             or (providers_checked[1] <> providers_checked[3]
                 and providers_checked[2] <> providers_checked[3]))
        and (cardinality(providers_checked) < 4
             or (providers_checked[1] <> providers_checked[4]
                 and providers_checked[2] <> providers_checked[4]
                 and providers_checked[3] <> providers_checked[4]))
    );
