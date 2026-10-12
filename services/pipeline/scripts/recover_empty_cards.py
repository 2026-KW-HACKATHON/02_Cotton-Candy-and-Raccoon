"""One bounded regeneration pass for publicly empty four-card summaries."""

import json
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from pipeline.config import DatabaseSettings

EMPTY = """
coalesce(btrim(v.card_summaries->>'audience'), '') = ''
and coalesce(btrim(v.card_summaries->>'deadline'), '') = ''
and coalesce(btrim(v.card_summaries->>'action'), '') = ''
and coalesce(btrim(v.card_summaries->>'notes'), '') = ''
"""
CHILD = """
import json, sys
from pipeline.config import DatabaseSettings
from pipeline.summary_run import summarize_one
from pipeline.processing_runner import run_processing
from pipeline.transform.gemini_prompt import load_gemini_api_key
db = DatabaseSettings.from_env()
key = load_gemini_api_key()
if sys.argv[2] == 'stopped':
    result = run_processing(db, api_key=key, features=('summary',),
                            notice_id=int(sys.argv[1]), retry_stopped=True,
                            job_timeout_seconds=240, lease_seconds=300)
    report = result.report()
else:
    result = summarize_one(db, int(sys.argv[1]), api_key=key)
    report = result.report()
    report.pop('view', None)
print(json.dumps(report, ensure_ascii=True))
"""


def snapshot(conn):
    return conn.execute(f"""
        select v.id, v.display_status,
               coalesce(j.state, 'missing') as job_state,
               s.last_error_code,
               coalesce(s.result->>'category', 'unknown') as category,
               coalesce(s.result->>'summary' = '원문 확인 필요', false) as fallback
        from public.app_notice_list v
        left join public.notice_summaries s on s.notice_id=v.id
        left join public.notice_processing_jobs j
          on j.notice_id=v.id and j.feature='summary'
        where {EMPTY}
        order by case when j.state in ('blocked', 'exhausted') then 1 else 0 end, v.id
    """).fetchall()


def main():
    database = DatabaseSettings.from_env()
    with psycopg.connect(database.database_url, autocommit=True, connect_timeout=10,
                          row_factory=dict_row) as conn:
        before = snapshot(conn)
        report = {'before': before, 'attempts': []}
        target = Path('empty-card-recovery.json')

        def save():
            target.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding='utf-8')

        save()
        print(json.dumps({'empty_before': len(before), 'targets': before}), flush=True)
        if len(before) > 100:
            raise RuntimeError('recovery_target_limit_exceeded')
        for item in before:
            current = {row['id']: row for row in snapshot(conn)}.get(item['id'])
            if current is None or current['job_state'] == 'running':
                continue
            mode = 'stopped' if current['job_state'] in ('blocked', 'exhausted') else 'cached'
            outcome = {'notice_id': item['id'], 'mode': mode}
            try:
                completed = subprocess.run(
                    [sys.executable, '-c', CHILD, str(item['id']), mode],
                    capture_output=True, text=True, timeout=300, check=False,
                )
                outcome['exit_code'] = completed.returncode
                # Never log raw stderr, API responses, source text or credentials.
                try:
                    outcome['result'] = json.loads(completed.stdout)
                except (ValueError, TypeError):
                    outcome['error'] = 'worker_failed_without_safe_report'
            except subprocess.TimeoutExpired:
                outcome['error'] = 'recovery_worker_timeout'
            remaining = {row['id'] for row in snapshot(conn)}
            outcome['cards_filled'] = item['id'] not in remaining
            report['attempts'].append(outcome)
            save()
            print(json.dumps(outcome, ensure_ascii=True), flush=True)
        report['after'] = snapshot(conn)
        report['filled_count'] = sum(item['cards_filled'] for item in report['attempts'])
        save()
        print(json.dumps({'attempted': len(report['attempts']),
                          'filled': report['filled_count'],
                          'empty_after': len(report['after'])}), flush=True)
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
