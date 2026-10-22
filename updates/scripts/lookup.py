#!/usr/bin/env python3
"""Read-only lookup over a bounded local delta, with explicit coverage warnings."""
import argparse,json,sqlite3
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('database');p.add_argument('query');p.add_argument('--kind',choices=['company','policy','paper'],default='company');a=p.parse_args()
c=sqlite3.connect('file:'+str(Path(a.database).resolve())+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
q=a.query;esc=q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_');pat='%'+esc+'%'
if a.kind=='company':
 rows=c.execute('''SELECT DISTINCT c.source_id,c.name,c.name_en,c.uscc,c.city,c.region,c.status,
  json_extract(c.raw,'$._refresh.source_url') AS source_url,
  json_extract(c.raw,'$._refresh.fetched_at') AS fetched_at,
  CASE WHEN c.uscc=? OR c.source_id=? OR c.name=? OR al.alias=? THEN 'exact_identifier_or_source_alias' ELSE 'substring_candidate_only' END AS match_method
 FROM company c LEFT JOIN company_alias al ON al.lei=c.lei
 WHERE c.uscc=? OR c.source_id=? OR c.name LIKE ? ESCAPE '\\' OR al.alias LIKE ? ESCAPE '\\'
 ORDER BY match_method,c.name LIMIT 20''',(q,q,q,q,q,q,pat,pat)).fetchall()
elif a.kind=='policy':
 rows=c.execute('''SELECT s.title,r.*,s.source_tier,s.pub_date,s.written_date,s.fetched_at FROM policy_rule r JOIN policy_source s USING(url)
 WHERE s.title LIKE ? ESCAPE '\\' OR r.evidence_quote LIKE ? ESCAPE '\\' LIMIT 30''',(pat,pat)).fetchall()
else:
 rows=c.execute('''SELECT paper_id,title,doi,pub_date,json_extract(raw,'$._refresh.source_url') AS source_url FROM paper WHERE title LIKE ? ESCAPE '\\' LIMIT 20''',(pat,)).fetchall()
print(json.dumps({'query':q,'kind':a.kind,'coverage':'仅本次增量快照，不是原库或全国全量；无命中不等于不存在、不在名单或没有专利。','policy_warning':'政策日期和金额只作为带来源的证据；转载必须官方核验，截止日期不表示政策当前有效。','results':[dict(r) for r in rows]},ensure_ascii=False,indent=2))
