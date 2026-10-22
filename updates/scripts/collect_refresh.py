#!/usr/bin/env python3
"""Collect bounded China-focused public-data refresh; no credentials in output."""
import argparse,datetime,gzip,hashlib,json,os,re,time
from pathlib import Path
import requests
BASE=Path(__file__).resolve().parent
TODAY=datetime.datetime.now(datetime.timezone.utc).date().isoformat()
S=requests.Session();S.headers['User-Agent']='qzd-public-refresh/1.0 (+https://github.com/ljyzmy/shuju)'
ALPH='0123456789ABCDEFGHJKLMNPQRTUWXY';WEIGHTS=[1,3,9,27,19,26,16,17,20,29,25,13,8,24,10,30,28]
def uscc_ok(s):
 return bool(s and len(s)==18 and all(c in ALPH for c in s) and ALPH[(31-sum(ALPH.index(c)*w for c,w in zip(s[:17],WEIGHTS))%31)%31]==s[-1])
def sid(s):return s.rsplit('/',1)[-1] if s else None
def dump(path,rows):
 path.parent.mkdir(parents=True,exist_ok=True)
 with gzip.open(path,'wt',encoding='utf-8') as f:
  for x in rows:f.write(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n')
def get(url,params=None):
 for attempt in range(3):
  r=S.get(url,params=params,timeout=60)
  if r.status_code in (429,500,502,503,504):
   if attempt==2:r.raise_for_status()
   time.sleep(min(20,2**attempt*2));continue
  r.raise_for_status();return r.json(),r.url
 raise RuntimeError('request exhausted')
def collect(out,cn_limit=1000,suzhou_limit=4000,paper_limit=600):
 out=Path(out);out.mkdir(parents=True,exist_ok=True)
 fetched=datetime.datetime.now(datetime.timezone.utc).isoformat();audit=[];errors=[];companies={};works={}
 batches=[('cn_recent',{'filter[entity.legalAddress.country]':'CN'},cn_limit),('suzhou_fulltext',{'filter[entity.legalAddress.country]':'CN','filter[fulltext]':'苏州'},suzhou_limit)]
 for label,flt,limit in batches:
  got=0;page=1
  while got<limit:
   params={**flt,'page[size]':min(200,limit-got),'page[number]':page,'sort':'-registration.lastUpdateDate'}
   try:d,url=get('https://api.gleif.org/api/v1/lei-records',params)
   except Exception as e:errors.append({'source':label,'error':str(e)});break
   rows=d.get('data',[])
   audit.append({'source':label,'url':url,'meta':d.get('meta'),'fetched_at':fetched,'rows':len(rows)})
   for r in rows:
    a=r['attributes'];ent=a['entity'];address=ent.get('legalAddress') or {}
    if address.get('country')!='CN':raise ValueError('country filter violated')
    companies[r['id']]={'source':'gleif','source_id':r['id'],'fetched_at':fetched,'source_url':r.get('links',{}).get('self') or 'https://api.gleif.org/api/v1/lei-records/'+r['id'],'raw':r}
   got+=len(rows);print(label,got,flush=True)
   if not rows or not d.get('links',{}).get('next'):break
   page+=1;time.sleep(1)
 page=1;got=0
 while got<paper_limit:
  params={'filter':'institutions.country_code:cn,from_publication_date:'+TODAY[:4]+'-01-01,to_publication_date:'+TODAY,'sort':'publication_date:desc','per-page':min(200,paper_limit-got),'page':page}
  if os.environ.get('OPENALEX_API_KEY'):params['api_key']=os.environ['OPENALEX_API_KEY']
  try:d,url=get('https://api.openalex.org/works',params)
  except Exception as e:errors.append({'source':'openalex','error':str(e)});break
  # Record a redacted URL even when an optional user-provided key is used.
  url=re.sub(r'([?&]api_key=)[^&]+',r'\1REDACTED',url)
  rows=d.get('results',[]);audit.append({'source':'openalex','url':url,'rows':len(rows),'total':d.get('meta',{}).get('count'),'fetched_at':fetched})
  for w in rows:
   if w.get('publication_date','9999')>TODAY:raise ValueError('future publication')
   if not any(i.get('country_code')=='CN' for a in w.get('authorships',[]) for i in a.get('institutions',[])):raise ValueError('CN affiliation missing')
   works[sid(w['id'])]={'paper_id':sid(w['id']),'fetched_at':fetched,'source_url':w['id'],'raw':w}
  got+=len(rows);print('openalex',got,flush=True)
  if not rows:break
  page+=1;time.sleep(1)
 dump(out/'company_gleif.jsonl.gz',sorted(companies.values(),key=lambda r:r['source_id']))
 dump(out/'paper_openalex.jsonl.gz',sorted(works.values(),key=lambda r:r['paper_id']))
 result={'fetched_at':fetched,'as_of':TODAY,'company_records':len(companies),'paper_records':len(works),'coverage':'latest 1000 CN LEI records plus bounded CN fulltext matches for 苏州 (not necessarily registered in Suzhou); recent works with at least one CN affiliation; NOT a full national registry or full publication corpus','requests':audit,'errors':errors}
 (out/'collection_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
 print(json.dumps({k:v for k,v in result.items() if k!='requests'},ensure_ascii=False))
 return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--out',default=str(BASE/'../data/refresh'));p.add_argument('--cn-limit',type=int,default=1000);p.add_argument('--suzhou-limit',type=int,default=4000);p.add_argument('--paper-limit',type=int,default=600);a=p.parse_args();collect(a.out,a.cn_limit,a.suzhou_limit,a.paper_limit)
