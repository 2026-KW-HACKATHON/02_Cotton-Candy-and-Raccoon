-- Gemini가 제안한 단어 치환만 반영한 본문을 원문과 분리해 저장한다.
create function public.notice_easy_text_revision(notice_title text, notice_body text)
returns text language sql immutable parallel safe as $$
    select encode(sha256(convert_to(
        '[' || to_json(notice_title)::text || ',' ||
        coalesce(to_json(notice_body)::text, 'null') || ']', 'UTF8'
    )), 'hex');
$$;

create table public.notice_easy_texts (
    notice_id bigint primary key references public.notices(id) on delete cascade,
    notice_revision text not null check (notice_revision ~ '^[0-9a-f]{64}$'),
    source_hash text not null check (source_hash ~ '^[0-9a-f]{64}$'),
    original_text text not null check (length(original_text) between 1 and 100000),
    easy_text text not null check (length(easy_text) > 0),
    changes jsonb not null check (jsonb_typeof(changes) = 'array'),
    model text not null check (length(btrim(model)) > 0),
    prompt_version text not null check (length(btrim(prompt_version)) > 0),
    attempt_count smallint not null check (attempt_count in (1, 2)),
    generated_at timestamptz not null,
    constraint notice_easy_text_hash check (
        encode(sha256(convert_to(original_text, 'UTF8')), 'hex') = source_hash
    )
);

comment on table public.notice_easy_texts is
    '원문을 보존한 Gemini 단어 치환 결과. 사전 정의·공식 다듬은 말 검증 결과와 구분한다.';
comment on column public.notice_easy_texts.changes is
    '원문 기준 start/end(유니코드 코드포인트), original, replacement, context. 바뀐 단어만 기록.';
comment on column public.notice_easy_texts.notice_revision is
    '제목·본문 HTML의 수집 버전. 변경된 공지의 이전 쉬운말 결과는 공개하지 않는다.';

alter table public.notice_easy_texts enable row level security;
revoke all on public.notice_easy_texts from public, anon, authenticated;
grant select on public.notice_easy_texts to anon, authenticated;
create policy "read current notice easy text" on public.notice_easy_texts
    for select to anon, authenticated using (exists (
        select 1 from public.notices n
        where n.id = notice_easy_texts.notice_id and n.is_visible
        and public.notice_easy_text_revision(n.title, n.body_html) = notice_easy_texts.notice_revision
    ));
