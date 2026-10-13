"""One-off, source-reviewed additions to empty slots; never replace existing prose."""
import copy
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from pipeline.config import DatabaseSettings
from pipeline.transform.summary_schema import GeminiCardSummaries, NoticeSummary


def validate_patch(p):
    after = p['after_result']
    NoticeSummary.model_validate(after)
    GeminiCardSummaries.model_validate(after['card_summaries'])
    before = p['before_result']
    if before is None:
        assert p['id'] == 49
        return
    expected = copy.deepcopy(before)
    for field, value in p['empty_fields'].items():
        assert field in {'audience', 'action', 'notes', 'dates', 'location'}
        assert not before.get(field), (p['id'], field)
        expected[field] = value
    for field, value in p['metadata'].items():
        assert field == 'action_requirement' and 'action' in p['empty_fields']
        expected[field] = value
    expected['evidence'] = before.get('evidence', []) + p['evidence']
    cards = dict(before.get('card_summaries') or {})
    for slot, value in p['cards'].items():
        assert not (cards.get(slot) or '').strip(), (p['id'], slot)
        cards[slot] = value
    expected['card_summaries'] = cards
    assert expected == after


def main():
    patches = json.loads(Path(__file__).with_name('fill_all_verified_empty.json').read_text(encoding='utf-8'))
    assert len({p['id'] for p in patches}) == len(patches) <= 40
    for p in patches:
        validate_patch(p)
    backup = []
    outcomes = []
    with psycopg.connect(DatabaseSettings.from_env().database_url,
                         connect_timeout=15, row_factory=dict_row) as conn:
        conn.execute("set local lock_timeout='10s'")
        conn.execute("set local statement_timeout='30s'")
        for p in sorted(patches, key=lambda p: p['id']):
            i = p['id']
            assert conn.execute('select id from public.notices where id=%s and is_visible for update', (i,)).fetchone()
            source = conn.execute('select title,body_text,files from public.app_notice_detail where id=%s', (i,)).fetchone()
            assert source['title'] == p['title'] and source['files'] == p['files'], f'Source changed: {i}'
            body = source['body_text'] or ''
            assert hashlib.sha256(body.encode()).hexdigest() == p['body_sha256'], f'Body changed: {i}'
            for evidence in p['evidence']:
                if evidence['source_type'] == 'text':
                    assert evidence['excerpt'] in body, f'Missing source evidence: {i}'
            row = conn.execute('select * from public.notice_summaries where notice_id=%s for update', (i,)).fetchone()
            assert row, f'Missing summary row: {i}'
            if row['result'] == p['after_result']:
                outcomes.append({'id': i, 'already_applied': True})
                continue
            assert row['result'] == p['before_result'], f'Concurrent summary change: {i}'
            running = conn.execute("select 1 from public.notice_processing_jobs where notice_id=%s and feature='summary' and state='running'", (i,)).fetchone()
            assert not running, f'Processing in progress: {i}'
            backup.append(row)
            if p['before_result'] is None:
                # The failed attempt already carries the current source identity.
                # Keep its partial/unread attachment metadata and expose a review result.
                assert row['status'] in {'failed', 'pending'}
                conn.execute("""update public.notice_summaries
                    set result=%s, status='needs_review', category=%s, category_code=%s,
                        deadline_on=null, model='manual-source-review',
                        generated_at=now(), last_error_code=null, updated_at=now()
                    where notice_id=%s""",
                    (Jsonb(p['after_result']), p['after_result']['category'], p['after_result']['category_code'], i))
            else:
                conn.execute('update public.notice_summaries set result=%s,updated_at=now() where notice_id=%s',
                             (Jsonb(p['after_result']), i))
            stored = conn.execute('select result from public.notice_summaries where notice_id=%s', (i,)).fetchone()
            assert stored['result'] == p['after_result']
            outcomes.append({'id': i, 'filled_slots': list(p['cards'])})
        # Save the complete preimage before the transaction is committed.
        Path('verified-empty-backup.json').write_text(json.dumps(backup, ensure_ascii=False, default=str, indent=2), encoding='utf-8')
    report = {'notices_written': sum('filled_slots' in x for x in outcomes),
              'cards_filled': sum(len(x.get('filled_slots', [])) for x in outcomes), 'outcomes': outcomes}
    Path('verified-empty-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
