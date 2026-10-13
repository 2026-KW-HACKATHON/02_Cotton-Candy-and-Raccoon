"""One-off, source-reviewed corrections for the frozen 2026-10-12 notice set.

No collection, model calls, schema migrations or application deployment.
All writes are guarded against the reviewed source/result snapshot and atomic.
"""
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from pipeline.config import DatabaseSettings
from pipeline.glossary.easy_language import ReviewedRewrite, flatten_easy_rewrite
from pipeline.glossary.rewrite_validation import validate_minimal_rewrite
from pipeline.storage.notice_easy_text import get_notice_easy_text_cache_token
from pipeline.transform.summary_schema import GeminiCardSummaries, NoticeSummary


def validate(patch, body):
    if 'cards' in patch:
        GeminiCardSummaries.model_validate(patch['cards'])
        result = dict(patch['before_result'], card_summaries=patch['cards'])
        NoticeSummary.model_validate(result)
    if 'easy_result' in patch:
        rewrite = ReviewedRewrite.model_validate(patch['easy_result'])
        validate_minimal_rewrite(rewrite, body)
        assert all(len(s.text) <= 60 for s in rewrite.sentences()), 'Sentence exceeds prompt limit'
        assert len(rewrite.headline) <= 60


def main():
    patches = json.loads(Path(__file__).with_name('recent_manual_repairs.json').read_text(encoding='utf-8'))
    assert len({p['id'] for p in patches}) == len(patches) == 16
    report, backup = [], []
    with psycopg.connect(DatabaseSettings.from_env().database_url,
                         connect_timeout=15, row_factory=dict_row) as conn:
        conn.execute("set local lock_timeout = '10s'")
        conn.execute("set local statement_timeout = '30s'")
        for p in sorted(patches, key=lambda p: p['id']):
            i = p['id']
            assert conn.execute('select id from public.notices where id=%s and is_visible for update', (i,)).fetchone()
            current = conn.execute('select * from public.app_notice_detail where id=%s', (i,)).fetchone()
            assert current and current['title'] == p['title'], f'Source missing/changed: {i}'
            assert hashlib.sha256((current['body_text'] or '').encode()).hexdigest() == p['body_sha256'], f'Body changed: {i}'
            assert current['files'] == p['before_files'], f'Files changed: {i}'
            validate(p, current['body_text'] or '')
            saved = {'id':i, 'cards':False, 'easy_text':False}
            old = {'id':i}
            if 'cards' in p:
                old['summary'] = conn.execute('select * from public.notice_summaries where notice_id=%s for update', (i,)).fetchone()
                assert old['summary'] and old['summary']['result'] == p['before_result'], f'Summary changed: {i}'
            if 'easy_result' in p:
                old['easy_text'] = conn.execute('select * from public.notice_easy_texts where notice_id=%s for update', (i,)).fetchone()
                e = old['easy_text']
                assert e and e['easy_result'] == p['before_easy_result'] and e['easy_text'] == p['before_easy_text'], f'Easy text changed: {i}'
                assert e['original_text'] == p['title']+'\n'+current['body_text'], f'Easy source mismatch: {i}'
                assert e['changes'] == [], f'Legacy easy text: {i}'
                old['dictionary'] = conn.execute('select * from public.notice_dictionary_links where notice_id=%s for update', (i,)).fetchone()
                old_token = get_notice_easy_text_cache_token(conn, i)
            backup.append(old)
            # Save rollback data before changing the corresponding row.
            Path('recent-manual-repairs-backup.json').write_text(json.dumps(backup,ensure_ascii=False,default=str),encoding='utf-8')
            if 'cards' in p:
                result = dict(p['before_result'], card_summaries=p['cards'])
                conn.execute("update public.notice_summaries set result=%s, status='needs_review', updated_at=now() where notice_id=%s", (Jsonb(result),i))
                saved['cards'] = True
            if 'easy_result' in p:
                rewrite = ReviewedRewrite.model_validate(p['easy_result'])
                text = p['title']+'\n'+flatten_easy_rewrite(rewrite)
                conn.execute('update public.notice_easy_texts set easy_result=%s,easy_text=%s where notice_id=%s',
                             (Jsonb(rewrite.model_dump(mode='json')),text,i))
                # Source text and its word positions are unchanged. Restore exactly
                # the old candidates invalidated by the generic update trigger.
                conn.execute('update public.notice_easy_texts set dictionary_candidates=%s where notice_id=%s',
                             (Jsonb(e['dictionary_candidates']) if e['dictionary_candidates'] is not None else None,i))
                link = old['dictionary']
                if link and link['easy_text_token'] == old_token and all(
                    c.get('mapping_status') == 'original_only' and c.get('easy_start') is None
                    and c.get('easy_end') is None for c in link['candidates']
                ):
                    conn.execute('update public.notice_dictionary_links set easy_text_token=%s where notice_id=%s',
                                 (get_notice_easy_text_cache_token(conn,i),i))
                saved['easy_text'] = True
            check = conn.execute('select card_summaries,easy_result,easy_text from public.app_notice_detail where id=%s',(i,)).fetchone()
            if saved['cards']: assert check['card_summaries'] == p['cards']
            if saved['easy_text']:
                assert check['easy_result'] == rewrite.model_dump(mode='json') and check['easy_text'] == text
            report.append(saved)
        # Context manager commits only after every source guard and verification passed.
    Path('recent-manual-repairs-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'committed':True,'notices':len(report),'cards':sum(r['cards'] for r in report),'easy_text':sum(r['easy_text'] for r in report)}))


if __name__ == '__main__':
    main()
