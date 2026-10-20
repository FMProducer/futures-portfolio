"""Derive the isolated 24h study wrapper from the accepted funding wrapper."""
from pathlib import Path

HERE=Path(__file__).resolve().parent
source=(HERE.parent/'funding_forward_2026-10-17/runner.py').read_text(encoding='utf-8')
replacements={
    'One bounded public-paper funding window; immutable runtime source snapshot.':
        'One bounded 24-hour public-paper window; immutable runtime source snapshot.',
    'duration=(due-now+999)//1000+300':'duration=86400',
    'post_funding_seconds=300':'study_hours=24,minimum_funding_events=3,minimum_span_ms=23*3600000',
    "'at least one exact target funding observation'":"'first target funding and at least three distinct funding timestamps; span at least 23 hours'",
    "    if any(r['result'] is None for r in rows): failures.append('pending input preserved')":
        "    times={v['fundingTime'] for row in events for v in row['payload']['funding']}\n"
        "    if len(times)<f['minimum_funding_events']: failures.append('fewer than three distinct funding events')\n"
        "    span=rows[-1]['payload']['sample']['after']-rows[0]['payload']['sample']['after'] if rows else 0\n"
        "    if span<f['minimum_span_ms']: failures.append('insufficient wall-clock observation span')\n"
        "    if any(r['result'] is None for r in rows): failures.append('pending input preserved')",
    "observations=len(rows),revision=revision,published_funding_events=len(events),":
        "observations=len(rows),revision=revision,published_funding_events=len(events),span_ms=span,",
    'reports.funding_forward_2026-10-17.runner':'reports.day_retry_2026-10-18.runner',
}
for old,new in replacements.items():
    assert source.count(old)==1, old
    source=source.replace(old,new)
target=HERE/'runner.py'
if target.exists(): raise ValueError('runner already generated')
target.write_text(source,encoding='utf-8')

