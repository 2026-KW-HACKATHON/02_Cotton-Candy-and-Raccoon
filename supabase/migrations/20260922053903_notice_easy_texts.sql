-- ============================================================================
-- notice_easy_texts: 쉬운말 결과
--
-- notices migration 다음에 적용한다. 요약 migration과는 서로 의존하지 않는다.
-- Gemini가 제안한 단어 치환만 반영한 본문을 원문과 분리해 저장한다.
-- 제목은 수집 원문 그대로 유지하고, 본문이 있는 결과만 공개한다.
-- ============================================================================
create function public.notice_easy_text_revision(notice_title text, notice_body text)
returns text language sql immutable parallel safe as $$
    select encode(sha256(convert_to(
        '[' || to_json(notice_title)::text || ',' ||
        coalesce(to_json(notice_body)::text, 'null') || ']', 'UTF8'
    )), 'hex');
$$;

create function public.notice_easy_text_preserves_title(
    notice_title text, original_text text, easy_text text, changes jsonb
)
returns boolean language plpgsql immutable parallel safe as $$
declare
    title_prefix text;
    body_start integer;
    item jsonb;
begin
    if notice_title is null or original_text is null or easy_text is null
       or jsonb_typeof(changes) is distinct from 'array'
    then
        return false;
    end if;

    title_prefix := notice_title || E'\n';
    body_start := char_length(title_prefix);
    if left(original_text, body_start) is distinct from title_prefix
       or left(easy_text, body_start) is distinct from title_prefix
       or substring(original_text from body_start + 1) !~ '[^[:space:]]'
       or substring(easy_text from body_start + 1) !~ '[^[:space:]]'
    then
        return false;
    end if;

    for item in select value from jsonb_array_elements(changes)
    loop
        if jsonb_typeof(item) is distinct from 'object'
           or jsonb_typeof(item -> 'start') is distinct from 'number'
        then
            return false;
        end if;
        -- JSON 숫자를 numeric으로 비교해 큰 값에서도 정수 변환 오류를 내지 않는다.
        if (item ->> 'start')::numeric < body_start then
            return false;
        end if;
    end loop;
    return true;
end;
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
    -- true: 제목+본문 평문, false: 제목만 처리, null: 처리 범위를 확인하지 못한 과거 결과.
    body_text_present boolean,
    -- 현재 입력은 첨부 내용을 포함하지 않는다(false). 첨부 존재 여부가 아니다.
    attachment_content_included boolean,
    constraint notice_easy_text_hash check (
        encode(sha256(convert_to(original_text, 'UTF8')), 'hex') = source_hash
    ),
    constraint notice_easy_text_scope_check check (
        (body_text_present is null and attachment_content_included is null)
        or (body_text_present is not null and attachment_content_included is false)
    )
);

-- 범위 컬럼을 모르는 구버전 작업자가 원문을 바꾸어 저장하면 과거 범위를 붙이지 않는다.
create function public.invalidate_notice_easy_text_scope()
returns trigger language plpgsql as $$
begin
    if (new.notice_revision is distinct from old.notice_revision
        or new.original_text is distinct from old.original_text)
       and new.body_text_present is not distinct from old.body_text_present
       and new.attachment_content_included is not distinct from old.attachment_content_included
    then
        new.body_text_present := null;
        new.attachment_content_included := null;
    end if;
    return new;
end;
$$;

create trigger invalidate_notice_easy_text_scope
    before update on public.notice_easy_texts
    for each row execute function public.invalidate_notice_easy_text_scope();


-- ============================================================================
-- 앱 읽기 권한
-- ============================================================================

-- 보이는 공지의 현재 버전 결과이고, 제목이 원문 그대로이며 본문이 있는 것만 읽는다.
-- policy가 호출하는 notice_easy_text_revision, notice_easy_text_preserves_title은
-- 요청한 역할의 권한으로 실행되므로 anon, authenticated의 실행 권한을 회수하지 않는다.
alter table public.notice_easy_texts enable row level security;
revoke all on public.notice_easy_texts from public, anon, authenticated;
grant select on public.notice_easy_texts to anon, authenticated;
create policy "read current notice easy text" on public.notice_easy_texts
    for select to anon, authenticated using (exists (
        select 1 from public.notices n
        where n.id = notice_easy_texts.notice_id and n.is_visible
        and public.notice_easy_text_revision(n.title, n.body_html) = notice_easy_texts.notice_revision
        and public.notice_easy_text_preserves_title(
            n.title, notice_easy_texts.original_text, notice_easy_texts.easy_text,
            notice_easy_texts.changes
        )
    ));


-- ============================================================================
-- 설명(comment)
-- ============================================================================
comment on table public.notice_easy_texts is
    '원문을 보존한 Gemini 단어 치환 결과. 사전 정의·공식 다듬은 말 검증 결과와 구분한다.';
comment on column public.notice_easy_texts.changes is
    '원문 기준 start/end(유니코드 코드포인트), original, replacement, context. 바뀐 단어만 기록.';
comment on column public.notice_easy_texts.notice_revision is
    '제목·본문 HTML의 수집 버전. 변경된 공지의 이전 쉬운말 결과는 공개하지 않는다.';
comment on column public.notice_easy_texts.body_text_present is
    'true: 제목+본문 평문, false: 제목만 처리, null: 처리 범위를 아직 확인하지 못함.';
comment on column public.notice_easy_texts.attachment_content_included is
    '현재 DB 입력은 첨부 내용 미포함(false). 첨부 존재 여부가 아님. null은 확인 전 과거 결과.';
comment on function public.notice_easy_text_preserves_title(text, text, text, jsonb) is
    '제목과 줄바꿈을 원문 그대로 유지하고 모든 변경이 본문에서 시작하는지 확인한다.';
