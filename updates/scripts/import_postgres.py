#!/usr/bin/env python3
"""Import validated SQLite delta into existing qzd PostgreSQL. DSN comes only from env.
Default: evidence/snapshot tables only. --merge-core explicitly opts into non-null core UPSERT.
No policy excerpt is promoted to policy.full_text; no fuzzy company linking is performed.
"""
import argparse,datetime,gzip,hashlib,json,os,sqlite3
from pathlib import Path
from build_lookup import lei_ok
DDL='''CREATE SCHEMA IF NOT EXISTS qzd_refresh;
CREATE TABLE IF NOT EXISTS qzd_refresh.snapshot (
 source text NOT NULL,source_id text NOT NULL,source_url text NOT NULL,
 source_updated_at timestamptz,fetched_at timestamptz NOT NULL,payload jsonb NOT NULL,sha256 text NOT NULL,
 PRIMARY KEY(source,source_id));
CREATE TABLE IF NOT EXISTS qzd_refresh.company_alias (
 lei text NOT NULL,alias text NOT NULL,language text,alias_type text,source_url text NOT NULL,
 PRIMARY KEY(lei,alias));
CREATE INDEX IF NOT EXISTS company_alias_lookup ON qzd_refresh.company_alias(alias);
CREATE TABLE IF NOT EXISTS qzd_refresh.policy_evidence (
 url text NOT NULL,rule_key text NOT NULL,kind text,subject_scope text,deadline_date date,value_text text,
 evidence_quote text NOT NULL,verification_status text NOT NULL,PRIMARY KEY(url,rule_key));
CREATE TABLE IF NOT EXISTS qzd_refresh.paper_author_affiliation (
 paper_id text NOT NULL,person_id text NOT NULL,institution_id text NOT NULL,
 PRIMARY KEY(paper_id,person_id,institution_id));
'''
def run(folder,merge=False,dry=False):
 folder=Path(folder);v=json.loads((folder/'validation.json').read_text())
 for name,expected in v['hashes'].items():
  if hashlib.sha256((folder/name).read_bytes()).hexdigest()!=expected:raise ValueError('Input hash mismatch: '+name)
 if hashlib.sha256((folder/'qzd_lookup.sqlite').read_bytes()).hexdigest()!=v['sqlite_sha256']:raise ValueError('SQLite hash mismatch; rebuild and validate first')
 db=sqlite3.connect('file:'+str((folder/'qzd_lookup.sqlite').resolve())+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
 if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('SQLite integrity failed')
 for t,n in v['counts'].items():
  if db.execute('SELECT count(*) FROM '+t).fetchone()[0]!=n:raise ValueError('SQLite count mismatch: '+t)
 if dry:
  print(json.dumps({'dry_run':True,'counts':v['counts'],'merge_core':merge,'note':'input hashes, counts and SQLite validated; no PostgreSQL connection attempted'},ensure_ascii=False,indent=2));return
 import psycopg2
 from psycopg2.extras import Json
 dsn=os.environ.get('QZD_DSN')
 if not dsn:raise SystemExit('Set QZD_DSN through environment; never hardcode a password.')
 def js(s):return Json(json.loads(s)) if s is not None else None
 counts={}
 # One transaction: errors roll back all changes; no DROP/DELETE/TRUNCATE.
 with psycopg2.connect(dsn) as con:
  with con.cursor() as cur:
   cur.execute(DDL)
   for file,source in [('company_gleif.jsonl.gz','gleif'),('paper_openalex.jsonl.gz','openalex'),('policy_evidence.jsonl.gz','policy_excerpt')]:
    with gzip.open(folder/file,'rt',encoding='utf-8') as f:
     for line in f:
      r=json.loads(line);key=r.get('source_id') or r.get('paper_id') or r.get('url')
      if source=='gleif' and not lei_ok(key):continue
      updated=((r.get('raw') or {}).get('attributes',{}).get('registration') or {}).get('lastUpdateDate') if source=='gleif' else None
      url=r.get('source_url') or r.get('url');payload=json.dumps(r,ensure_ascii=False,sort_keys=True)
      cur.execute('''INSERT INTO qzd_refresh.snapshot VALUES(%s,%s,%s,%s,%s,%s,%s)
       ON CONFLICT(source,source_id) DO UPDATE SET source_url=excluded.source_url,source_updated_at=excluded.source_updated_at,
       fetched_at=excluded.fetched_at,payload=excluded.payload,sha256=excluded.sha256
       WHERE excluded.fetched_at>=qzd_refresh.snapshot.fetched_at
       AND (qzd_refresh.snapshot.source_updated_at IS NULL OR excluded.source_updated_at>=qzd_refresh.snapshot.source_updated_at)''',
       (source,key,url,updated,r['fetched_at'],Json(r),hashlib.sha256(payload.encode()).hexdigest()))
   for table in ['company_alias','policy_rule','paper_author_affiliation']:
    rows=db.execute('SELECT * FROM '+table).fetchall()
    dest={'company_alias':'company_alias','policy_rule':'policy_evidence','paper_author_affiliation':'paper_author_affiliation'}[table]
    for row in rows:
     columns=row.keys();pk={'company_alias':['lei','alias'],'policy_rule':['url','rule_key'],'paper_author_affiliation':['paper_id','person_id','institution_id']}[table]
     updates=[c+'=excluded.'+c for c in columns if c not in pk]
     sql='INSERT INTO qzd_refresh.'+dest+'('+','.join(columns)+') VALUES('+','.join(['%s']*len(columns))+') ON CONFLICT('+','.join(pk)+') DO '+('UPDATE SET '+','.join(updates) if updates else 'NOTHING')
     cur.execute(sql,tuple(row));counts[dest]=len(rows)
   if merge:
    # Minimal original schema fields only; preserve richer non-null old values.
    keys={'company':['source','source_id'],'institution':['institution_id'],'person':['person_id'],'paper':['paper_id'],'paper_author':['paper_id','person_id']}
    for table,pk in keys.items():
     changed=0
     for row in db.execute('SELECT * FROM '+table):
      cols=list(row.keys());values=[]
      for c in cols:
       val=row[c]
       if c=='raw':val=js(val)
       elif c=='concepts':val=json.loads(val) if val else None
       elif c=='is_oa':val=bool(val) if val is not None else None
       values.append(val)
      updates=[]
      for c in cols:
       if c in pk:continue
       if c=='raw':updates.append('raw=COALESCE('+table+'.raw,\'{}\'::jsonb)||excluded.raw')
       else:updates.append(c+'=COALESCE(excluded.'+c+','+table+'.'+c+')')
      guard=''
      if table=='company':
       guard=" WHERE COALESCE((company.raw->'_refresh'->>'fetched_at')::timestamptz,'-infinity'::timestamptz)<=excluded.updated_at AND COALESCE((company.raw->'_refresh'->>'source_updated_at')::timestamptz,'-infinity'::timestamptz)<=COALESCE((excluded.raw->'_refresh'->>'source_updated_at')::timestamptz,'-infinity'::timestamptz)"
      elif table=='paper':
       guard=" WHERE COALESCE((paper.raw->'_refresh'->>'fetched_at')::timestamptz,'-infinity'::timestamptz)<=((excluded.raw->'_refresh'->>'fetched_at')::timestamptz)"
      sql='INSERT INTO public.'+table+'('+','.join(cols)+') VALUES('+','.join(['%s']*len(cols))+') ON CONFLICT('+','.join(pk)+') DO UPDATE SET '+','.join(updates)+guard
      cur.execute(sql,values);changed+=cur.rowcount
     counts[table]=changed
   print(json.dumps({'committed_on_success':True,'core_merge':merge,'affected_rows':counts,'note':'Affected rows include existing updates, not net-new counts.'},ensure_ascii=False,indent=2))
 db.close()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('folder');p.add_argument('--merge-core',action='store_true');p.add_argument('--dry-run',action='store_true');a=p.parse_args();run(a.folder,a.merge_core,a.dry_run)
