-- 제목은 수집 원문 그대로 유지하고, 본문이 있는 쉬운말 결과만 공개한다.
-- 구버전 작업자가 저장한 제목 치환 결과도 삭제하지 않고 공개 조회에서 제외한다.
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

comment on function public.notice_easy_text_preserves_title(text, text, text, jsonb) is
    '제목과 줄바꿈을 원문 그대로 유지하고 모든 변경이 본문에서 시작하는지 확인한다.';

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
    ));
