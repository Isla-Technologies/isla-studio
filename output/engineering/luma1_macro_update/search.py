"""Finite hardware optimization against the existing policy, with measured Macro limits.
No hardware operation. All input provenance and every candidate outcome are saved.
"""
import os
for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']: os.environ[key]='1'
from pathlib import Path
import sys,json,copy,hashlib,math,argparse,time
from concurrent.futures import ProcessPoolExecutor,as_completed
import numpy as np

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(OUT.parent/'luma1_review'))
import run_review as rr
from isla_rl.learn.policy import Policy

MACRO=OUT/'macro_original.json'
M=json.loads(MACRO.read_text(encoding='utf-8')) if MACRO.exists() else None
def inputs():
    return {v['key']:v['value'] for v in M['values']}

def cases():
    v=inputs()
    return [dict(name='nominal',mass=30,torque=v['peak_torque'],corner=v['corner_speed'],no_load=v['no_load_speed'],inertia=.16,friction=.81,damping=.25,mu=.6),
            dict(name='adverse',mass=30,torque=.75*v['peak_torque'],corner=6.5,no_load=v['no_load_speed']*40/48,inertia=.25,friction=1.2,damping=.4,mu=.4,push_time=3.3)]

def candidate(t=42,s=46,kf=26,kb=29,cf=380,cb=410,pf=1,pb=1.5,stroke=32):
    return {'design':dict(thigh_rod=float(t),shin_rod=float(s),front_k=float(kf),back_k=float(kb),front_c=float(cf),back_c=float(cb),front_preload=float(pf),back_preload=float(pb)),'stroke':float(stroke)}

def worker(item):
    idx,c=item
    # The standalone PD stand heuristic is irrelevant to these unassisted policy tests.
    rr.EngineeringBench.__module__
    from isla_rl.sim.session import LiveSession
    LiveSession._stands_alone=lambda self:False
    rows=[]
    for case in cases():
        case={**case,'stroke':c['stroke']}
        if c.get('compression_extra_mm'):case['compression_extra_mm']=c['compression_extra_mm']
        rows.append(rr.trial(c['design'],case,seconds=9,push=(0,.2) if case['name']=='adverse' else None))
    bad=0; rms=[];peak=[];err=[]
    for r in rows:
        ms=[m for m in r['motors'] if m['type']=='macro' and ('hip' in m['joint'] or 'knee' in m['joint'])]
        v=r['movement']['forward'];margin=r['stroke_clearance_mm']
        failed=not r['survived'] or not r['settled'] or r['gantry_active']
        bad+=1000*failed+200*max(0,.22-v,v-.38)+100*max(0,abs(r['movement']['left'])-.06)
        bad+=50*max(0,2-(margin if margin is not None else -100)) + 10*max(0,r['max_tilt_deg']-20)
        bad+=500*max(0,max((m['saturated_share'] for m in ms),default=1)-.02)
        rms.append(max((m['torque_rms'] for m in ms),default=100))
        peak.append(max((m['torque_peak'] for m in ms),default=100))
        err.append(abs(v-.3))
    score=bad+max(rms)+.12*max(peak)+20*max(err)+.05*c['stroke']+.002*(310+c['design']['thigh_rod']+c['design']['shin_rod'])
    return {'id':idx,'candidate':c,'score':float(score),'feasible':bad<1e-9,'max_leg_rms':max(rms),'max_leg_peak':max(peak),'max_speed_error':max(err),'trials':rows}

def run(pool,filename):
    rows=[];start=time.time()
    with ProcessPoolExecutor(max_workers=4) as ex:
        futures=[ex.submit(worker,(i,c)) for i,c in enumerate(pool)]
        for future in as_completed(futures):
            r=future.result();rows.append(r)
            (OUT/filename).write_text(json.dumps(sorted(rows,key=lambda x:x['score']),indent=2,allow_nan=False))
            print(f"{len(rows)}/{len(pool)} best={min(x['score'] for x in rows):.3f} feasible={sum(x['feasible'] for x in rows)} elapsed={time.time()-start:.0f}s",flush=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['coarse','refine']);args=parser.parse_args()
    rng=np.random.default_rng(20261109)
    if args.mode=='coarse':
        pool=[candidate()]
        pool.append({**candidate(stroke=30),'compression_extra_mm':2})
        for t in [0,20,42,60]:
            for s in [0,25,46,70]:pool.append(candidate(t=t,s=s))
        for _ in range(46):
            stroke=int(rng.choice([28,32,36]));kf=float(rng.choice(np.arange(20,32.5,.5)));kb=float(rng.choice(np.arange(22,35.5,.5)))
            pf=round(float(np.clip(rng.uniform(390,460)/kf-stroke/2,.5,6))*2)/2
            pb=round(float(np.clip(rng.uniform(440,520)/kb-stroke/2,.5,6))*2)/2
            pool.append(candidate(t=int(rng.integers(5,76)),s=int(rng.integers(5,86)),kf=kf,kb=kb,cf=int(rng.integers(50,111))*5,cb=int(rng.integers(50,121))*5,pf=pf,pb=pb,stroke=stroke))
        run(pool,'coarse.json')
    else:
        old=json.loads((OUT/'coarse.json').read_text());pool=[r['candidate'] for r in old[:6]]
        keys=['thigh_rod','shin_rod','front_k','back_k','front_c','back_c','front_preload','back_preload']
        scales=[8,8,1.5,1.5,55,55,.5,.5];lo=[-20,-20,18,20,150,150,.5,.5];hi=[85,90,36,38,600,600,6,6];step=[1,1,.5,.5,5,5,.5,.5]
        for _ in range(42):
            c=copy.deepcopy(old[int(rng.integers(0,6))]['candidate']);c.pop('compression_extra_mm',None)
            for k,sd,l,h,st in zip(keys,scales,lo,hi,step):c['design'][k]=float(np.clip(round((c['design'][k]+rng.normal()*sd)/st)*st,l,h))
            pool.append(c)
        run(pool,'refine.json')

if __name__=='__main__':main()
