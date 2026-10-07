-- Preserve Gemini card text inside the full result and expose the same value for
-- convenient app reads. A generated column prevents duplicate writes drifting.
-- Missing/null legacy cards and rows without a result remain SQL NULL.
alter table public.notice_summaries
  add column card_summaries jsonb
  generated always as (nullif(result -> 'card_summaries', 'null'::jsonb)) stored;

-- Exactly four named slots; each is JSON null or a nonblank single-line string.
-- CASE guards scalar JSON before subtraction, and COALESCE closes SQL UNKNOWN
-- bypasses. Include Python str.strip() Unicode whitespace explicitly because
-- PostgreSQL's [[:space:]] classification depends on the database locale.
alter table public.notice_summaries
  add constraint notice_summaries_card_summaries_ck check (
    case
      when card_summaries is null then true
      when jsonb_typeof(card_summaries) = 'object' then
        card_summaries ?& array['audience', 'deadline', 'action', 'notes']
        and card_summaries - array['audience', 'deadline', 'action', 'notes'] = '{}'::jsonb
        and coalesce(
          jsonb_typeof(card_summaries -> 'audience') = 'null'
          or (
            jsonb_typeof(card_summaries -> 'audience') = 'string'
            and card_summaries ->> 'audience' !~ U&'^[[:space:]\001C\001D\001E\001F\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000]*$'
            and card_summaries ->> 'audience' !~ '[\r\n]'
          ), false
        )
        and coalesce(
          jsonb_typeof(card_summaries -> 'deadline') = 'null'
          or (
            jsonb_typeof(card_summaries -> 'deadline') = 'string'
            and card_summaries ->> 'deadline' !~ U&'^[[:space:]\001C\001D\001E\001F\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000]*$'
            and card_summaries ->> 'deadline' !~ '[\r\n]'
          ), false
        )
        and coalesce(
          jsonb_typeof(card_summaries -> 'action') = 'null'
          or (
            jsonb_typeof(card_summaries -> 'action') = 'string'
            and card_summaries ->> 'action' !~ U&'^[[:space:]\001C\001D\001E\001F\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000]*$'
            and card_summaries ->> 'action' !~ '[\r\n]'
          ), false
        )
        and coalesce(
          jsonb_typeof(card_summaries -> 'notes') = 'null'
          or (
            jsonb_typeof(card_summaries -> 'notes') = 'string'
            and card_summaries ->> 'notes' !~ U&'^[[:space:]\001C\001D\001E\001F\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000]*$'
            and card_summaries ->> 'notes' !~ '[\r\n]'
          ), false
        )
      else false
    end
  );

comment on column public.notice_summaries.card_summaries is
  'Generated from result.card_summaries: audience/deadline/action/notes, each string or JSON null. Legacy missing/null cards become SQL NULL; needs_review keeps the original-notice warning.';

-- Existing visible-notice RLS and private metadata restrictions are unchanged.
-- The app receives SELECT only; result remains the backend's sole write source.
grant select (card_summaries) on public.notice_summaries to anon, authenticated;
