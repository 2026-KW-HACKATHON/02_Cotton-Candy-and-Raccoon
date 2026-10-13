"""Undo the overbroad preceding edit, then fill only originally empty card slots."""
import json
import hashlib
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pipeline.config import DatabaseSettings
from pipeline.storage.notice_easy_text import get_notice_easy_text_cache_token
from pipeline.transform.summary_schema import GeminiCardSummaries

BASE=Path(__file__).parent
def read(name):return json.loads((BASE/name).read_text(encoding='utf-8'))

def main():
    backup=read('revert_recent_repairs_backup.json')
    patches={p['id']:p for p in read('recent_manual_repairs.json')}
    fills=read('fill_recent_empty_only.json')
    snapshots=read('fill_recent_empty_snapshot.json')
    counts=[]
    with psycopg.connect(DatabaseSettings.from_env().database_url,connect_timeout=15,row_factory=dict_row) as conn:
        conn.execute("set local lock_timeout='10s'")
        # Lock the complete affected set in ascending order.
        for i in sorted(set(patches)|{int(k) for k in fills}):
            assert conn.execute('select id from public.notices where id=%s for update',(i,)).fetchone()
        for old in backup:
            i=old['id']; p=patches[i]
            if 'summary' in old:
                s=conn.execute('select result from public.notice_summaries where notice_id=%s for update',(i,)).fetchone()
                expected=dict(p['before_result'],card_summaries=p['cards'])
                assert s['result']==expected, f'Concurrent summary change: {i}'
                b=old['summary']
                conn.execute('update public.notice_summaries set result=%s,status=%s,updated_at=%s where notice_id=%s',
                             (Jsonb(b['result']),b['status'],b['updated_at'],i))
            if 'easy_text' in old:
                e=conn.execute('select * from public.notice_easy_texts where notice_id=%s for update',(i,)).fetchone()
                b=old['easy_text']
                assert e['easy_result']==p['easy_result'] and e['original_text']==b['original_text'],f'Concurrent easy change: {i}'
                conn.execute('update public.notice_easy_texts set easy_text=%s,easy_result=%s where notice_id=%s',
                             (b['easy_text'],Jsonb(b['easy_result']),i))
                conn.execute('update public.notice_easy_texts set dictionary_candidates=%s where notice_id=%s',
                             (Jsonb(b['dictionary_candidates']) if b['dictionary_candidates'] is not None else None,i))
                if old.get('dictionary'):
                    link=conn.execute('select * from public.notice_dictionary_links where notice_id=%s for update',(i,)).fetchone()
                    if link and link['candidates']==old['dictionary']['candidates']:
                        conn.execute('update public.notice_dictionary_links set easy_text_token=%s where notice_id=%s',
                                     (old['dictionary']['easy_text_token'],i))
        for key, additions in fills.items():
            i=int(key)
            view=conn.execute('select body_text,title,files from public.app_notice_detail where id=%s',(i,)).fetchone()
            snap=snapshots[key]
            assert view and view['title']==snap['title'] and view['files']==snap['files'],f'Source changed: {i}'
            assert hashlib.sha256((view['body_text'] or '').encode()).hexdigest()==snap['body_sha256'],f'Body changed: {i}'
            row=conn.execute('select result from public.notice_summaries where notice_id=%s for update',(i,)).fetchone()
            assert row and row['result'],f'Missing result: {i}'
            data=row['result']; cards=dict(data.get('card_summaries') or {})
            assert cards==snap['cards'],f'Card snapshot changed: {i}'
            for slot,text in additions.items():
                assert not (cards.get(slot) or '').strip(), f'Nonempty card: {i}/{slot}'
                cards[slot]=text
            GeminiCardSummaries.model_validate(cards)
            data['card_summaries']=cards
            conn.execute('update public.notice_summaries set result=%s,updated_at=now() where notice_id=%s',(Jsonb(data),i))
            counts.append({'id':i,'filled_slots':list(additions)})
        # All changes are committed together, only if every guard passed.
    print(json.dumps({'restored_notices':len(backup),'filled':counts}))

if __name__=='__main__':main()
