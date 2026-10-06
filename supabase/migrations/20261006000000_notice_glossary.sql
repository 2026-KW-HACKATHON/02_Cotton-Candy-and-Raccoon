-- 공지 원문과 공식 사전 근거에 따른 용어 치환 결과를 함께 보존한다.
-- 동일한 원문·처리 규칙의 미완료 결과는 성공한 조회를 유지하며 이어서 처리한다.
create table public.notice_glossary_results (
    notice_id bigint primary key references public.notices(id) on delete cascade,
    source_hash text not null check (source_hash ~ '^[0-9a-f]{64}$'),
    rules_version text not null check (length(btrim(rules_version)) > 0),
    generated_at timestamptz not null,
    status text not null check (status in ('completed', 'partial')),
    result jsonb not null,
    constraint notice_glossary_result_shape check (
        jsonb_typeof(result) = 'object'
        and result ?& array[
            'notice_id', 'source_hash', 'rules_version', 'generated_at',
            'status', 'original_text', 'easy_text'
        ]
        and jsonb_typeof(result -> 'notice_id') = 'number'
        and jsonb_typeof(result -> 'source_hash') = 'string'
        and jsonb_typeof(result -> 'rules_version') = 'string'
        and jsonb_typeof(result -> 'generated_at') = 'string'
        and jsonb_typeof(result -> 'status') = 'string'
        and jsonb_typeof(result -> 'original_text') = 'string'
        and jsonb_typeof(result -> 'easy_text') = 'string'
        and length(result ->> 'original_text') between 1 and 100000
        and length(result ->> 'easy_text') > 0
    ),
    constraint notice_glossary_result_metadata check (
        result ->> 'notice_id' = notice_id::text
        and result ->> 'source_hash' = source_hash
        and result ->> 'rules_version' = rules_version
        and (result ->> 'generated_at')::timestamptz = generated_at
        and result ->> 'status' = status
        and encode(sha256(convert_to(result ->> 'original_text', 'UTF8')), 'hex') = source_hash
    )
);

comment on table public.notice_glossary_results is
    '공지별 원문과 쉬운말 버전, 위치별 치환 및 사전 근거. 실패한 개별 조회와 남은 조회어를 구분한다.';
comment on column public.notice_glossary_results.source_hash is
    '공백과 유니코드 정규화를 유지한 정확한 원문의 UTF-8 SHA-256.';
comment on column public.notice_glossary_results.result is
    'original_text와 easy_text를 분리하고 성공한 사전 조회·출처·남은 작업을 보존한다.';

alter table public.notice_glossary_results enable row level security;
revoke all on public.notice_glossary_results from public, anon, authenticated;
grant select on public.notice_glossary_results to anon, authenticated;
create policy "read notice glossary results" on public.notice_glossary_results
    for select to anon, authenticated using (exists (
        select 1 from public.notices n
        where n.id = notice_glossary_results.notice_id and n.is_visible
    ));
