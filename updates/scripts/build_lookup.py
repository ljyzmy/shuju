#!/usr/bin/env python3
"""Build a self-contained SQLite delta; never labels a miss as absence."""
import argparse,datetime,gzip,hashlib,json,re,sqlite3
from pathlib import Path
from collect_refresh import uscc_ok,sid
SCHEMA='''
CREATE TABLE company(source TEXT,source_id TEXT,name TEXT,name_en TEXT,uscc TEXT,lei TEXT,reg_number TEXT,country TEXT,region TEXT,city TEXT,address TEXT,postal_code TEXT,legal_form TEXT,status TEXT,established_date TEXT,raw TEXT,updated_at TEXT,PRIMARY KEY(source,source_id));
CREATE TABLE company_alias(lei TEXT,alias TEXT,language TEXT,alias_type TEXT,source_url TEXT,PRIMARY KEY(lei,alias));
CREATE INDEX company_alias_name ON company_alias(alias);
CREATE INDEX company_uscc ON company(uscc);
CREATE TABLE paper(paper_id TEXT PRIMARY KEY,doi TEXT,title TEXT,abstract TEXT,pub_year INT,pub_date TEXT,type TEXT,venue TEXT,language TEXT,is_oa INT,cited_by_count INT,concepts TEXT,raw TEXT);
CREATE TABLE institution(institution_id TEXT PRIMARY KEY,ror TEXT,name TEXT,country TEXT,type TEXT);
CREATE TABLE person(person_id TEXT PRIMARY KEY,source TEXT,name TEXT,orcid TEXT);
CREATE TABLE paper_author(paper_id TEXT,person_id TEXT,position INT,institution_id TEXT,PRIMARY KEY(paper_id,person_id));
CREATE TABLE paper_author_affiliation(paper_id TEXT,person_id TEXT,institution_id TEXT,PRIMARY KEY(paper_id,person_id,institution_id));
CREATE TABLE policy_source(url TEXT PRIMARY KEY,title TEXT,source_tier TEXT,verification_status TEXT,pub_date TEXT,written_date TEXT,fetched_at TEXT,raw TEXT);
CREATE TABLE policy_rule(url TEXT,rule_key TEXT,kind TEXT,subject_scope TEXT,deadline_date TEXT,value_text TEXT,evidence_quote TEXT,verification_status TEXT,PRIMARY KEY(url,rule_key));
CREATE TABLE coverage(key TEXT PRIMARY KEY,value TEXT);
'''
def j(x):return json.dumps(x,ensure_ascii=False,sort_keys=True)
def records(p):
 with gzip.open(p,'rt',encoding='utf-8') as f:
  for line in f:yield json.loads(line)
def lei_ok(s):
 if not re.fullmatch('[A-Z0-9]{20}',s or ''):return False
 return int(''.join(str(ord(c)-55) if c.isalpha() else c for c in s))%97==1
def build(folder,target):
 folder=Path(folder);target=Path(target)
 # Write to a separate file then replace only after a successful validation.
 temp=target.with_suffix('.building.sqlite');temp.unlink(missing_ok=True)
 db=sqlite3.connect(temp);db.executescript(SCHEMA)
 bad_uscc=[];bad_lei=[];countries=set();source_dates=[]
 for row in records(folder/'company_gleif.jsonl.gz'):
  a=row['raw']['attributes'];e=a['entity'];addr=e.get('legalAddress') or {};reg=e.get('registeredAs');reg=(reg or '').upper()
  if not lei_ok(row['source_id']):
   bad_lei.append(row['source_id']);continue
  if len(reg)==18 and not uscc_ok(reg):bad_uscc.append({'lei':row['source_id'],'registration_id':reg})
  aliases=[{**(e.get('legalName') or {}),'type':'LEGAL_NAME'}]+e.get('otherNames',[])+e.get('transliteratedOtherNames',[])
  name=(e.get('legalName') or {}).get('name')
  en=next((x['name'] for x in aliases if x.get('language','').startswith('en') and x.get('name')),None)
  updated=(a.get('registration') or {}).get('lastUpdateDate');source_dates.append(updated)
  raw={'_gleif':row['raw'],'_refresh':{'fetched_at':row['fetched_at'],'source_updated_at':updated,'source_url':row['source_url']}}
  vals=('gleif',row['source_id'],name,en,reg if uscc_ok(reg) else None,row['source_id'],reg or None,addr.get('country'),addr.get('region'),addr.get('city'),' '.join(addr.get('addressLines') or []) or None,addr.get('postalCode'),(e.get('legalForm') or {}).get('otherLegalForm') or (e.get('legalForm') or {}).get('id'),e.get('status'),(e.get('creationDate') or '')[:10] or None,j(raw),row['fetched_at'])
  db.execute('INSERT INTO company VALUES('+','.join('?' for _ in vals)+')',vals);countries.add(addr.get('country'))
  for n in aliases:
   if n.get('name'):db.execute('INSERT OR IGNORE INTO company_alias VALUES(?,?,?,?,?)',(row['source_id'],n['name'],n.get('language'),n.get('type'),row['source_url']))
 for row in records(folder/'paper_openalex.jsonl.gz'):
  w=row['raw'];src=((w.get('primary_location') or {}).get('source') or {});ab=w.get('abstract_inverted_index');abstract=' '.join(word for pos,word in sorted((p,k) for k,v in ab.items() for p in v)) if ab else None
  vals=(row['paper_id'],(w.get('doi') or '').removeprefix('https://doi.org/') or None,w.get('title'),abstract,w.get('publication_year'),w.get('publication_date'),w.get('type'),src.get('display_name'),w.get('language'),(w.get('open_access') or {}).get('is_oa'),w.get('cited_by_count'),j([t['display_name'] for t in w.get('topics',[])]),j({'_openalex':w,'_refresh':{'fetched_at':row['fetched_at'],'source_url':row['source_url']}}))
  db.execute('INSERT INTO paper VALUES('+','.join('?' for _ in vals)+')',vals)
  for pos,auth in enumerate(w.get('authorships',[])):
   au=auth.get('author') or {};aid=sid(au.get('id'))
   if not aid:continue
   db.execute('INSERT OR IGNORE INTO person VALUES(?,?,?,?)',(aid,'openalex',au.get('display_name') or aid,au.get('orcid')))
   insts=auth.get('institutions') or []
   for ins in insts:
    iid=sid(ins.get('id'))
    if not iid:continue
    db.execute('INSERT OR IGNORE INTO institution VALUES(?,?,?,?,?)',(iid,ins.get('ror'),ins.get('display_name') or iid,ins.get('country_code'),ins.get('type')))
    db.execute('INSERT OR IGNORE INTO paper_author_affiliation VALUES(?,?,?)',(row['paper_id'],aid,iid))
   db.execute('INSERT OR IGNORE INTO paper_author VALUES(?,?,?,?)',(row['paper_id'],aid,pos,sid(insts[0].get('id')) if insts else None))
 for p in records(folder/'policy_evidence.jsonl.gz'):
  db.execute('INSERT INTO policy_source VALUES(?,?,?,?,?,?,?,?)',(p['url'],p['title'],p['source_tier'],p['verification_status'],p['pub_date'],p['written_date'],p['fetched_at'],j(p)))
  for r in p['rules']:db.execute('INSERT INTO policy_rule VALUES(?,?,?,?,?,?,?,?)',(p['url'],r['rule_key'],r['kind'],r['subject_scope'],r['deadline_date'],r['value_text'],r['evidence_quote'],p['verification_status']))
 manifest=json.loads((folder/'collection_manifest.json').read_text());db.execute('INSERT INTO coverage VALUES(?,?)',('manifest',j(manifest)))
 db.execute('INSERT INTO coverage VALUES(?,?)',('negative_result_warning','未命中仅代表本快照未收录，不能证明企业不在名单、没有资质或没有专利。'))
 db.execute('INSERT INTO coverage VALUES(?,?)',('policy_warning','政策数据为带来源的原文片段，不是全文；转载待官方核验；日期不代表当前仍有效。'))
 integrity=db.execute('PRAGMA integrity_check').fetchone()[0];tables=['company','company_alias','paper','institution','person','paper_author','paper_author_affiliation','policy_source','policy_rule']
 counts={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in tables}
 future=db.execute('SELECT count(*) FROM paper WHERE pub_date>?',(manifest['as_of'],)).fetchone()[0]
 orphans=db.execute('SELECT count(*) FROM paper_author a LEFT JOIN paper p USING(paper_id) LEFT JOIN person n USING(person_id) WHERE p.paper_id IS NULL OR n.person_id IS NULL').fetchone()[0]
 if integrity!='ok' or countries!={'CN'} or future or orphans:raise ValueError(j({'integrity':integrity,'bad_lei':bad_lei,'countries':list(countries),'future':future,'orphans':orphans}))
 db.commit();db.close();temp.replace(target)
 report={'counts':counts,'sqlite_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'sqlite_integrity':integrity,'invalid_lei':bad_lei,'invalid_18_char_registration_ids_not_promoted_to_uscc':bad_uscc,'future_papers':future,'orphan_paper_authors':orphans,'company_countries':sorted(countries),'source_update_min':min(x for x in source_dates if x),'source_update_max':max(x for x in source_dates if x),'hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob('*.jsonl.gz')},'limitation':'Delta records may already exist in the baseline. Counts are fetched distinct records, NOT net new baseline rows. Baseline dump not rewritten. Policy excerpts do not imply completeness or legal effectiveness.'}
 (folder/'validation.json').write_text(j(report));print(json.dumps(report,ensure_ascii=False,indent=2));return report
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('folder');p.add_argument('--out',required=True);a=p.parse_args();build(a.folder,a.out)
