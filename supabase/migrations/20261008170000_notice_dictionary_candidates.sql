-- 같은 Gemini 결과에서 얻은 사전 검색 후보. null은 미처리, []는 후보 없음이다.
-- 사전 조회 결과나 캐시가 아니라 원문에 근거한 검색어와 위치만 저장한다.
alter table public.notice_easy_texts add column dictionary_candidates jsonb;

create function public.notice_dictionary_candidates_valid(
    original_text text, candidates jsonb, body_start integer default 0
)
returns boolean language plpgsql immutable parallel safe as $$
declare
    item jsonb;
    start_offset integer;
    end_offset integer;
    context_offset integer;
    original_in_context integer;
    previous_end integer := body_start;
begin
    if candidates is null then
        return true;
    end if;
    if jsonb_typeof(candidates) is distinct from 'array' or original_text is null then
        return false;
    end if;
    for item in select value from jsonb_array_elements(candidates)
    loop
        if jsonb_typeof(item) is distinct from 'object'
           or jsonb_typeof(item -> 'original') is distinct from 'string'
           or jsonb_typeof(item -> 'query_word') is distinct from 'string'
           or jsonb_typeof(item -> 'context') is distinct from 'string'
           or jsonb_typeof(item -> 'start') is distinct from 'number'
           or jsonb_typeof(item -> 'end') is distinct from 'number'
        then
            return false;
        end if;
        if (select count(*) from jsonb_object_keys(item)) != 5
           or length(item ->> 'original') not between 1 and 100
           or length(item ->> 'query_word') not between 1 and 100
           or length(item ->> 'context') not between 1 and 100000
           or btrim(item ->> 'original') = '' or btrim(item ->> 'query_word') = ''
           or (item ->> 'start') !~ '^(0|[1-9][0-9]*)$'
           or (item ->> 'end') !~ '^(0|[1-9][0-9]*)$'
        then
            return false;
        end if;
        -- numeric으로 먼저 범위를 검사해 큰 JSON 정수에서도 변환 오류를 피한다.
        if (item ->> 'start')::numeric < previous_end
           or (item ->> 'end')::numeric <= (item ->> 'start')::numeric
           or (item ->> 'end')::numeric > char_length(original_text)
        then
            return false;
        end if;
        start_offset := (item ->> 'start')::integer;
        end_offset := (item ->> 'end')::integer;
        original_in_context := strpos(item ->> 'context', item ->> 'original');
        context_offset := start_offset - original_in_context + 1;
        if substring(original_text from start_offset + 1 for end_offset - start_offset)
               is distinct from item ->> 'original'
           or original_in_context = 0 or context_offset < body_start
           or substring(original_text from context_offset + 1 for length(item ->> 'context'))
               is distinct from item ->> 'context'
        then
            return false;
        end if;
        previous_end := end_offset;
    end loop;
    return true;
end;
$$;

alter table public.notice_easy_texts add constraint notice_dictionary_candidates_check
    check (public.notice_dictionary_candidates_valid(original_text, dictionary_candidates));

comment on column public.notice_easy_texts.dictionary_candidates is
    '사전 검색 후보 original/query_word/context/start/end. 원문 유니코드 코드포인트 위치. null=미처리, []=처리했으나 후보 없음.';

-- 후보 컬럼을 모르는 구버전 작업자가 새 결과에 과거 후보를 붙이지 않게 한다.
-- 같은 []/후보를 명시해도 컬럼 생략과 구분할 수 없으므로 새 저장 코드는
-- 성공한 UPSERT의 행 잠금을 유지한 같은 트랜잭션 안에서 검증된 후보를 복원한다.
create function public.invalidate_notice_dictionary_candidates()
returns trigger language plpgsql as $$
begin
    if (new.notice_revision is distinct from old.notice_revision
        or new.source_hash is distinct from old.source_hash
        or new.original_text is distinct from old.original_text
        or new.easy_text is distinct from old.easy_text
        or new.changes is distinct from old.changes
        or new.model is distinct from old.model
        or new.prompt_version is distinct from old.prompt_version
        or new.attempt_count is distinct from old.attempt_count
        or new.generated_at is distinct from old.generated_at)
       and new.dictionary_candidates is not distinct from old.dictionary_candidates
    then
        new.dictionary_candidates := null;
    end if;
    return new;
end;
$$;

create trigger invalidate_notice_dictionary_candidates
    before update on public.notice_easy_texts
    for each row execute function public.invalidate_notice_dictionary_candidates();

-- 제목 안의 후보와 제목을 문맥으로 사용한 후보를 공개하지 않는다.
drop policy "read current notice easy text" on public.notice_easy_texts;
create policy "read current notice easy text" on public.notice_easy_texts
    for select to anon, authenticated using (exists (
        select 1 from public.notices n
        where n.id = notice_easy_texts.notice_id and n.is_visible
        and public.notice_easy_text_revision(n.title, n.body_html) = notice_easy_texts.notice_revision
        and public.notice_easy_text_preserves_title(
            n.title, notice_easy_texts.original_text, notice_easy_texts.easy_text,
            notice_easy_texts.changes
        )
        and public.notice_dictionary_candidates_valid(
            notice_easy_texts.original_text, notice_easy_texts.dictionary_candidates,
            char_length(n.title || E'\n')
        )
    ));
