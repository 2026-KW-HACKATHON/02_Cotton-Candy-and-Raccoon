-- 처리 범위는 모델이 아니라 실제 제목·본문 입력에서 확인한다.
-- 과거 행의 범위를 추측하지 않는다. 검증된 캐시 재사용 시 API 호출 없이 보충한다.
alter table public.notice_easy_texts
    add column body_text_present boolean,
    add column attachment_content_included boolean,
    add constraint notice_easy_text_scope_check check (
        (body_text_present is null and attachment_content_included is null)
        or (body_text_present is not null and attachment_content_included is false)
    );

comment on column public.notice_easy_texts.body_text_present is
    'true: 제목+본문 평문, false: 제목만 처리, null: 처리 범위를 아직 확인하지 못함.';
comment on column public.notice_easy_texts.attachment_content_included is
    '현재 DB 입력은 첨부 내용 미포함(false). 첨부 존재 여부가 아님. null은 확인 전 과거 결과.';

-- 범위 컬럼을 모르는 구버전 작업자가 원문을 바꾸어 저장하면 과거 범위를 붙이지 않는다.
-- 새로운 코드도 원문 변경 후 같은 범위 값을 쓰면 저장 검증 후 범위만 다시 보충한다.
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
