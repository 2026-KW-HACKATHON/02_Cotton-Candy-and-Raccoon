"""Apply the explicitly requested, source-reviewed cards to notice 717 only."""
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from pipeline.config import DatabaseSettings
from pipeline.transform.summary_schema import NoticeSummary, GeminiCardSummaries


def main():
    patch = json.loads(Path(__file__).with_name('manual_cards_717.json').read_text(encoding='utf-8'))
    assert patch['notice_id'] == 717
    GeminiCardSummaries.model_validate(patch['cards'])
    with psycopg.connect(DatabaseSettings.from_env().database_url,
                         connect_timeout=15, row_factory=dict_row) as conn:
        notice = conn.execute('select body_text from public.notices where id=717 and is_visible for update').fetchone()
        assert notice and hashlib.sha256(notice['body_text'].encode()).hexdigest() == patch['body_sha256'], 'Source changed'
        row = conn.execute('select result from public.notice_summaries where notice_id=717 for update').fetchone()
        assert row and row['result'], 'Missing summary'
        data = row['result']
        if data.get('card_summaries') == patch['cards']:
            print('Notice 717 already contains the requested cards')
            return
        assert not any((data.get('card_summaries') or {}).values()), 'Cards changed since review'
        data['card_summaries'] = patch['cards']
        checked = NoticeSummary.model_validate(data)
        conn.execute("update public.notice_summaries set result=%s, status='needs_review', updated_at=now() where notice_id=717",
                     (Jsonb(checked.model_dump(mode='json')),))
    print('Saved four manually reviewed cards for notice 717')


if __name__ == '__main__':
    main()
