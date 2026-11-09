import os
for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']:os.environ[key]='1'
import json,copy,math,argparse,time
from concurrent.futures import ProcessPoolExecutor,as_completed
import numpy as np
from search import OUT,ROOT,rr,cases,candidate

def worker(task):
    from isla_rl.sim.session import LiveSession
    LiveSession._stands_alone=lambda self:False
    candidate,case,command,seconds,push=task
    case={**case,'stroke':candidate['stroke']}
    if candidate.get('compression_extra_mm'):case['compression_extra_mm']=candidate['compression_extra_mm']
    return rr.trial(candidate['design'],case,command,seconds,push)

def tasks(c,full=False):
    nom,adv=cases()
    rows=[(c,nom,(.3,0,0),30 if full else 15,None)]
    for pt in ([3,3.1,3.2,3.3] if full else [3.1]):
        for k in range(8):rows.append((c,{**adv,'push_time':pt},(.3,0,0),12,(k*math.pi/4,.2)))
    for command in [(0,0,0),(.2,0,0),(.4,0,0),(-.15,0,0),(0,.1,0),(0,-.1,0),(0,0,.3),(0,0,-.3),(.3,0,.3)]:
        rows.append((c,nom,command,15,None))
    if full:
        for j in [.05,.1,.2]:rows.append((c,{**adv,'name':'inertia','inertia':j},(.3,0,0),15,None))
        for name,changes in [('mu_0.3',{'mu':.3}),('torque_60pct',{'torque':nom['torque']*.6}),('33kg',{'mass':33})]:
            rows.append((c,{**adv,'name':name,**changes},(.3,0,0),15,None))
        for seed in range(8):
            rng=np.random.default_rng(9040+seed);v=copy.deepcopy(c)
            for key in ['front_k','back_k']:v['design'][key]*=float(rng.uniform(.9,1.1))
            for key in ['front_c','back_c']:v['design'][key]*=float(rng.uniform(.8,1.2))
            for key in ['front_preload','back_preload']:v['design'][key]=max(0,v['design'][key]+float(rng.uniform(-.5,.5)))
            rows.append((v,{**adv,'name':'tolerances','seed':9040+seed,'inertia':float(rng.uniform(.05,.25))},(.3,0,0),15,None))
    return rows

def summary(rows):
    ms=[m for r in rows for m in r['motors'] if m['type']=='macro' and ('hip' in m['joint'] or 'knee' in m['joint'])]
    return dict(trials=len(rows),falls=sum(not r['survived'] for r in rows),minimum_clearance=min(r['stroke_clearance_mm'] if r['stroke_clearance_mm'] is not None else -100 for r in rows),
                peak=max(m['torque_peak'] for m in ms),rms=max(m['torque_rms'] for m in ms),
                speed_error=max(abs(r['movement']['forward']-r['command'][0]) for r in rows if r['command'][0]>0 and not r['command'][2]))

def run_jobs(jobs,path):
    rows={};start=time.time()
    with ProcessPoolExecutor(max_workers=4) as ex:
        futures={ex.submit(worker,task):i for i,task in enumerate(jobs)}
        for f in as_completed(futures):
            rows[futures[f]]=f.result()
            path.write_text(json.dumps([rows[k] for k in sorted(rows)],indent=2,allow_nan=False))
            print(path.stem,len(rows),'/',len(jobs),'elapsed',round(time.time()-start),flush=True)
    return [rows[k] for k in sorted(rows)]

def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['shortlist','full','baseline']);args=p.parse_args()
    if args.mode=='baseline':
        c=candidate(pf=4,pb=4.5,stroke=24)
        rs=run_jobs(tasks(c),OUT/'baseline.json')
        (OUT/'baseline_summary.json').write_text(json.dumps(summary(rs),indent=2))
    elif args.mode=='shortlist':
        allrows=json.loads((OUT/'coarse.json').read_text())+json.loads((OUT/'refine.json').read_text())
        pool=[];seen=set()
        for r in sorted(allrows,key=lambda x:x['score']):
            key=json.dumps(r['candidate'],sort_keys=True)
            if r['feasible'] and key not in seen:pool.append(r);seen.add(key)
            if len(pool)==3:break
        summaries=[]
        for i,r in enumerate(pool):
            rs=run_jobs(tasks(r['candidate']),OUT/f'shortlist_{i}.json')
            s={**summary(rs),'candidate':r['candidate'],'screen_score':r['score'],'index':i}
            summaries.append(s);print('SHORTLIST',json.dumps(s),flush=True)
        (OUT/'shortlist.json').write_text(json.dumps(summaries,indent=2))
        acceptable=[s for s in summaries if s['falls']==0 and s['minimum_clearance']>=2 and s['speed_error']<=.1]
        if not acceptable:raise RuntimeError('No shortlisted design passed holdout criteria')
        chosen=min(acceptable,key=lambda s:s['screen_score'])
        (OUT/'selected.json').write_text(json.dumps(chosen,indent=2))
    else:
        selected=json.loads((OUT/'selected.json').read_text())
        rs=run_jobs(tasks(selected['candidate'],True),OUT/'validation.json')
        s=summary(rs);(OUT/'validation_summary.json').write_text(json.dumps(s,indent=2));print(s)

if __name__=='__main__':main()
