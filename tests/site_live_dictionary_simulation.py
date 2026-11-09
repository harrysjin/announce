"""Opt-in fresh official API simulation for the generated sentence cases.

Run: python3 tests/site_live_dictionary_simulation.py --live
The dictionary key comes from the environment; it is never printed.
This does not upload to the operating website or change its deployment.
"""
import argparse
import asyncio,hashlib,json,sys
from pathlib import Path
from datetime import datetime,timezone
import fitz
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.dictionary import DictionaryClient
from backend.main import analyze_job,mark_candidate,apply_number_result
from backend.numbers import evaluate_number
from backend.pdf import extract,output_pdf
OUT=ROOT/'test-results/re-audit/live-dictionary-after'
def make(target,context,nth=0):
 d=fitz.open();p=d.new_page();p.insert_text((45,100),context,fontname='korea',fontsize=14);raw=d.tobytes();d.close()
 pages,all_items=extract(raw);o=[x for x in all_items if x['text']==target][nth]
 return raw,{'id':'source-verification','filename':'검증용.pdf','sha256':hashlib.sha256(raw).hexdigest(),'pages':pages,'occurrences':[o],'marks':[],'settings':{'color':'#CEE4F5','opacity':.6,'size':1},'analysis':{'state':'running','done':0,'total':1,'message':'test'},'history':[],'revision':0,'warnings':[]}
async def run():
 client=DictionaryClient();rows=[]
 cases=[('보도부를','보도부를 통해 소식을 전합니다.',0,'long'),('선발하며','보도부원을 선발하며 활동합니다.',0,'unresolved'),('2명을','신입 부원 2명을 뽑습니다.',0,'long'),('4개를','물품 4개를 준비합니다.',0,'long'),('5개도','물품 5개도 준비합니다.',0,'none'),('2026년에는','2026년에는 새 부원을 뽑습니다.',0,'long'),('2','2 번 확인하고 2 명을 뽑습니다.',0,'unresolved'),('2','2 번 확인하고 2 명을 뽑습니다.',1,'long'),('24','24',0,'unresolved'),('말의','말의 길이를 살핍니다.',0,'unresolved'),('알아','그 뜻을 알아.',0,'none'),('끌어','수레를 끌어.',0,'long'),('첫눈','첫눈',0,'none'),('밀물','밀물',0,'none'),('사회혁신융합전공','사회혁신융합전공 신입생',0,'unresolved')]
 for index,(target,context,nth,expected) in enumerate(cases):
  raw,job=make(target,context,nth);job['_dictionary']=client
  await analyze_job(job);o=job['occurrences'][0]
  row={'text':target,'context':context,'occurrence_index':nth,'expected':expected,'status':o['status'],'reason_code':o.get('reason_code'),'reason':o['reason'],'lemma':o['lemma'],'evidence_type':o.get('evidence_type'),'analysis_state':job['analysis']['state'],'offsets':[o['glyph_ids'].index(m['glyph_id']) for m in job['marks']]}
  row['passed']=o['status']==expected and job['analysis']['state']=='complete'
  if target=='선발하며':
   chosen=next((c for c in o['candidates'] if '選拔' in c.get('origin','')),None)
   if chosen:
    mark_candidate(job,o,chosen,user_selected=True)
    row['simulated_user_meaning_choice']={'candidate_id':chosen['id'],'status':o['status'],'evidence_type':o['evidence_type'],'source':o['source']}
    row['passed'] &= o['status']=='long'
  elif target=='24':
   option=next(x for x in o['numeric']['options'] if x['system']=='sino')
   result=await evaluate_number(o['numeric'],client,option['id']);apply_number_result(job,o,result,user_selected=True)
   row['simulated_user_reading_choice']={'reading':option['reading'],'status':o['status'],'evidence_type':o['evidence_type'],'offsets':[o['glyph_ids'].index(m['glyph_id']) for m in job['marks']]}
   row['passed'] &= o['status']=='long' and len(job['marks'])==1
  row['sources']=[{k:c.get(k) for k in ('id','word','pos','url','queried_at')} for c in o['candidates'] if c['id'] in o.get('evidence_candidates',[])]
  (OUT/f'case-{index+1}.pdf').write_bytes(output_pdf(raw,job))
  rows.append(row);print(json.dumps({k:row[k] for k in ['text','occurrence_index','status','lemma','offsets','passed']},ensure_ascii=False),flush=True)
  (OUT/'report.json').write_text(json.dumps({'mode':'fresh_official_dictionary_API_with_local_engine','ran_at':datetime.now(timezone.utc).isoformat(),'cases':rows,'all_passed':all(r['passed'] for r in rows)},ensure_ascii=False,indent=2))
 assert all(r['passed'] for r in rows),'Inspect report.json'
if __name__ == '__main__':
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--live',action='store_true',help='Acknowledge real official API requests')
 parser.add_argument('--output',type=Path,default=OUT)
 args=parser.parse_args()
 if not args.live: parser.error('--live is required; normal regressions use offline fixtures')
 OUT=args.output.resolve();OUT.mkdir(parents=True,exist_ok=True)
 asyncio.run(run())
