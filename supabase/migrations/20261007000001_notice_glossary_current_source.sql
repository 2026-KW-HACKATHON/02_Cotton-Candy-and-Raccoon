-- 이전 사전 기반 쉬운말 결과도 현재 수집 원문과 일치할 때만 공개한다.
-- 원문 버전이 없는 과거 결과는 재처리 전까지 내부에 보존하고 공개하지 않는다.
drop policy "read notice glossary results" on public.notice_glossary_results;

create policy "read notice glossary results" on public.notice_glossary_results
    for select to anon, authenticated using (exists (
        select 1 from public.notices n
        where n.id = notice_glossary_results.notice_id and n.is_visible
        and jsonb_typeof(notice_glossary_results.result -> 'notice_revision') = 'string'
        and notice_glossary_results.result ->> 'notice_revision' ~ '^[0-9a-f]{64}$'
        and public.notice_easy_text_revision(n.title, n.body_html) =
            notice_glossary_results.result ->> 'notice_revision'
    ));
