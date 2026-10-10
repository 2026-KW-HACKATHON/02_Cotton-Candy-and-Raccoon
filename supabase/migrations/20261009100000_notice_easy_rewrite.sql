-- ============================================================================
-- 쉬운말을 단어 치환에서 질문형 재작성으로 바꾼다 (#85)
--
-- notice_easy_texts.easy_result에 Gemini가 다시 쓴 질문형 섹션(EasyRewrite)을 저장한다.
--   null  : 이전 세대(단어 치환) 결과. changes로 easy_text를 재현한다.
--   object: 재작성 결과. changes는 [], easy_text는 제목 + 줄바꿈 + 평문으로 펼친 글.
-- 문장별 근거, 숫자, 기호, 원문 복사 검사는 pipeline이 저장 전에 한다. DB는 형식만 본다.
--
-- 이 컬럼이 생기면 행 전체를 해시하는 쉬운말 상태 토큰이 모든 행에서 바뀐다.
-- 그래서 기존 notice_dictionary_links는 전부 이전 토큰을 가리키게 되고,
-- get_notice_dictionary는 다시 연결할 때까지 'pending'을 돌려준다.
-- 적용 후 process-stored로 쉬운말과 사전 링크를 다시 만든다(pipeline README).
-- ============================================================================

alter table public.notice_easy_texts add column easy_result jsonb;

alter table public.notice_easy_texts add constraint notice_easy_texts_easy_result_ck
    check (easy_result is null or jsonb_typeof(easy_result) = 'object');

comment on column public.notice_easy_texts.easy_result is
    '질문형 재작성 결과 {headline, intro, sections[{heading, style, sentences[{text, evidence}]}], attachment_hint}. null은 이전 단어 치환 결과.';


-- 결과를 모르는 구버전 작업자가 새 결과에 과거 후보를 붙이지 않게 비교 목록에 easy_result를 더한다.
create or replace function public.invalidate_notice_dictionary_candidates()
returns trigger language plpgsql as $$
begin
    if (new.notice_revision is distinct from old.notice_revision
        or new.source_hash is distinct from old.source_hash
        or new.original_text is distinct from old.original_text
        or new.easy_text is distinct from old.easy_text
        or new.changes is distinct from old.changes
        or new.easy_result is distinct from old.easy_result
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


-- easy_result를 모르는 구버전 작업자가 원문이나 변환을 바꾸면 남은 재작성 결과가
-- 새 easy_text와 맞지 않게 된다. 그런 갱신에서는 easy_result를 비워 이전 세대 행으로 둔다.
-- 같은 결과를 다시 저장해도 trigger에서는 생략과 구분할 수 없으므로, 새 저장 코드는
-- 성공한 UPSERT의 행 잠금을 유지한 같은 트랜잭션 안에서 검증된 결과를 복원한다.
create function public.invalidate_notice_easy_rewrite()
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
       and new.easy_result is not distinct from old.easy_result
    then
        new.easy_result := null;
    end if;
    return new;
end;
$$;

-- 이름 순서상 invalidate_notice_dictionary_candidates 다음, invalidate_notice_easy_text_scope 앞에 실행된다.
-- 이 trigger가 easy_result를 비우는 것은 변환 컬럼이 바뀐 갱신뿐이고, 그때는 사전 후보 trigger도
-- easy_result와 관계없이 조건을 만족한다. 범위 trigger가 바꾸는 컬럼은 두 trigger가 보지 않는다.
-- 따라서 실행 순서와 관계없이 결과가 같다.
create trigger invalidate_notice_easy_rewrite
    before update on public.notice_easy_texts
    for each row execute function public.invalidate_notice_easy_rewrite();

revoke all on function public.invalidate_notice_easy_rewrite() from public, anon, authenticated;


-- security_invoker view가 easy_result를 읽으므로 view보다 먼저 앱 역할에 컬럼 권한을 준다.
grant select (easy_result) on public.notice_easy_texts to anon, authenticated;


-- ----------------------------------------------------------------------------
-- app_notice_list: has_easy_text는 재작성 결과가 있을 때만 true다.
-- 이전 단어 치환 결과는 원문과 거의 같아 앱이 쉬운말 화면을 열 이유가 없다.
-- 나머지 컬럼과 조건은 20261008220000_app_notice_views.sql과 같다.
-- ----------------------------------------------------------------------------
create or replace view public.app_notice_list with (security_invoker = true) as
select
  n.id,
  n.category as source,
  n.dong_group,
  n.is_pinned,
  n.title,
  n.department,
  n.registered_on,
  n.content_updated_at,
  n.is_modified,
  s.status as summary_status,
  case
    when s.notice_id is null then 'none'
    when s.status = 'summarized'
      and jsonb_array_length(coalesce(s.preparation_omissions, '[]'::jsonb)) > 0
      then 'needs_review'
    else s.status
  end as display_status,
  s.category as notice_type,
  s.category_code,
  s.deadline_on,
  s.result ->> 'summary' as headline,
  s.card_summaries,
  s.attachment_status,
  exists (
    select 1 from public.notice_easy_texts e
    where e.notice_id = n.id and e.easy_result is not null
  ) as has_easy_text
from public.notices n
left join public.notice_summaries s on s.notice_id = n.id
where n.is_visible
  and n.dong_group is distinct from 'other';


-- ----------------------------------------------------------------------------
-- app_notice_detail: 맨 끝에 easy_result를 더한다. 기존 컬럼 순서는 바꾸지 않는다.
-- ----------------------------------------------------------------------------
create or replace view public.app_notice_detail with (security_invoker = true) as
select
  l.id,
  l.source,
  l.dong_group,
  l.is_pinned,
  l.title,
  l.department,
  l.registered_on,
  l.content_updated_at,
  l.is_modified,
  l.summary_status,
  l.display_status,
  l.notice_type,
  l.category_code,
  l.deadline_on,
  l.headline,
  l.card_summaries,
  l.attachment_status,
  l.has_easy_text,
  n.url,
  n.license_type,
  n.body_text,
  s.result,
  s.generated_at,
  s.file_references,
  s.preparation_omissions,
  coalesce((
    select jsonb_agg(jsonb_build_object('id', f.id, 'kind', f.kind, 'url', f.url) order by f.id)
    from public.notice_files f
    where f.notice_id = n.id
  ), '[]'::jsonb) as files,
  e.original_text as easy_original_text,
  e.easy_text,
  e.changes as easy_changes,
  e.body_text_present as easy_body_text_present,
  e.attachment_content_included as easy_attachment_content_included,
  e.generated_at as easy_generated_at,
  e.easy_result
from public.app_notice_list l
join public.notices n on n.id = l.id
left join public.notice_summaries s on s.notice_id = n.id
left join public.notice_easy_texts e on e.notice_id = n.id;


comment on column public.app_notice_list.has_easy_text is
  'True when the caller can read a current question-section rewrite (same revision, title preserved). Legacy replacement rows are false.';
comment on column public.app_notice_detail.easy_result is
  'Question-section rewrite {headline, intro, sections, attachment_hint}; NULL for legacy replacement rows. easy_text is the title plus its flattened text.';
