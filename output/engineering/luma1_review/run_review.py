"""Reproducible Luma-1 engineering screening; does not edit robot or policy files."""
from pathlib import Path
from dataclasses import replace
import sys, json, copy, time, hashlib, argparse
import numpy as np
import mujoco

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from isla_rl.robot.profile import RobotProfile
from isla_rl.robot.build import build, measure
from isla_rl.robot.analysis import analyse
from isla_rl.learn.policy import Policy
from isla_rl.learn.evaluate import Bench

OUT = Path(__file__).parent
BASE = RobotProfile.load(ROOT / 'robots/luma_1')
POL = Policy.load(ROOT / 'examples/runs/luma1-walk/policy.json')

class EngineeringBench(Bench):
    def begin(self):
        super().begin()
        self.eng_steps=0
        self.single=[0,0]
        self.touchdowns=[0,0]
        self.previous_contact=[True,True]
        self.load_peak=0.0
        self.mechanical_positive_peak=0.0
        self.mechanical_negative_peak=0.0
    def _record(self):
        super()._record()
        self.eng_steps+=1
        contact=[bool(self.data.sensordata[a]>5) for a in self.touch]
        for i in range(2):
            self.single[i]+=int(contact[i] and not contact[1-i])
            self.touchdowns[i]+=int(contact[i] and not self.previous_contact[i])
        self.previous_contact=contact
        self.load_peak=max(self.load_peak,self.foot_load())
        power=self.data.actuator_force*self.data.qvel[self.session.actuator_dofs]
        self.mechanical_positive_peak=max(self.mechanical_positive_peak,float(np.maximum(power,0).sum()))
        self.mechanical_negative_peak=max(self.mechanical_negative_peak,float(-np.minimum(power,0).sum()))

def make_profile(design, case):
    p = copy.deepcopy(BASE)
    # Builder adds changed rod mass after virtual ballast. Compensate to exact requested mass.
    cad = measure(p)
    rod_delta = sum(piece['mass'] * design[r['name']+'_rod']/1000/piece['length']
                    for r in cad['rods'] for piece in r['rods'])
    p.data['mass']['target_total'] = case.get('mass',30) - rod_delta
    if not case.get('legacy',False):
        p.data['actuator_types']['macro'].update(
            peak_torque=case.get('torque',44.731715),
            no_load_speed=case.get('no_load',8.90169),
            corner_speed=case.get('corner',6.5),
            armature=case.get('inertia',0.16),
            frictionloss=case.get('friction',0.81),
            damping=case.get('damping',0.25))
        p.data['actuator_types']['macro'].pop('continuous_current',None)
    p.data['struts']['stroke_mm'] = case.get('stroke',24)
    return p

def trial(design,case,command=(0.3,0,0),seconds=12,push=None):
    p=make_profile(design,case)
    policy=replace(POL,design=design)
    b=EngineeringBench(p,policy)
    # Extra compression travel is asymmetric: keep extended length and spring free length.
    extra=case.get('compression_extra_mm',0)/1000
    if extra:
        for shock in b.shocks:
            b.model.tendon_range[shock.tendon,0]-=extra
        for info in b.session.info.struts:
            info.length_min-=extra
    # Friction sensitivity applies to ground and soles; torsional/rolling coefficients unchanged.
    if 'mu' in case: b.model.geom_friction[:,0]=case['mu']
    b.reset()
    if extra:
        for shock in b.shocks: shock.low-=extra
    if 'seed' in case:
        rng=np.random.default_rng(case['seed'])
        b.data.qpos[b.session.actuator_qpos]+=rng.uniform(-0.01,0.01,len(b.session.actuator_qpos))
        mujoco.mj_forward(b.model,b.data)
    settled=not b.fallen() and not b.session.message
    b.command(*command)
    b.begin()
    w=b.start()
    if push is None:
        ok=b.run(seconds) if settled else False
    else:
        push_time=case.get('push_time',3)
        ok=b.run(push_time) if settled else False
        if ok:
            b.shove(push[0],push[1])
            ok=b.run(seconds-push_time)
    movement=b.since(w)
    shocks=[s.summary() for s in b.shocks if s.steps]
    motors=[m.summary() for m in b.motors if m.steps]
    result={'case':case,'design':design,'command':command,'push':push,'requested_seconds':seconds,
            'simulated_seconds':float(b.data.time-w.t),'settled':settled,'survived':bool(ok),
            'mass':b.mass,'movement':movement,'max_tilt_deg':b.max_tilt,'shocks':shocks,'motors':motors,
            'gantry_active':bool(b.data.eq_active[b.session.harness])}
    result['foot_contacts']={'touchdowns':b.touchdowns,'single_support_share':[v/max(1,b.eng_steps) for v in b.single],
        'ground_force_peak_bodyweights':b.load_peak}
    result['mechanical_power_W']={'positive_peak':b.mechanical_positive_peak,'negative_peak':b.mechanical_negative_peak}
    result['stroke_clearance_mm']=min((min(s['closest_to_bottom_mm'],s['closest_to_top_mm']) for s in shocks),default=None)
    return result

def compact(r):
    return {k:r[k] for k in ['survived','simulated_seconds','movement','max_tilt_deg','stroke_clearance_mm']}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['screen','geometry','adverse','validate','phase','final'])
    args=parser.parse_args()
    if args.mode=='geometry':
        p=make_profile(POL.design,{'mass':30})
        b=build(p,POL.design)
        a=analyse(p,b,measure(p),POL.design)
        (OUT/'geometry_existing.json').write_text(json.dumps(a,indent=2))
        print(json.dumps(a,indent=2))
        return
    rows=[]
    if args.mode=='final':
        source=json.loads((OUT/'validation.json').read_text())+json.loads((OUT/'phase.json').read_text())
        for original in source:
            case={**original['case'],'compression_extra_mm':2}
            r=trial(original['design'],case,original['command'],original['requested_seconds'],original['push'])
            rows.append(r)
            (OUT/'final.json').write_text(json.dumps(rows,indent=2))
            print(len(rows),case['name'],json.dumps(compact(r)),flush=True)
        return
    if args.mode=='phase':
        design={**POL.design,'front_preload':1,'back_preload':1.5}
        base={'name':'push_phase','mass':30,'inertia':0.25,'friction':1.2,'damping':0.4,'mu':0.4,'stroke':30}
        for push_time in [3.1,3.2,3.3]:
            for angle in np.arange(8)*np.pi/4:
                r=trial(design,{**base,'push_time':push_time},seconds=12,push=(float(angle),.2))
                rows.append(r)
                (OUT/'phase.json').write_text(json.dumps(rows,indent=2))
                print(len(rows),push_time,round(float(angle),2),json.dumps(compact(r)),flush=True)
        return
    if args.mode=='validate':
        design={**POL.design,'front_preload':1,'back_preload':1.5}
        base={'name':'compound_adverse','mass':30,'inertia':0.25,'friction':1.2,'damping':0.4,'mu':0.4,'stroke':30}
        tasks=[(base,design,(0.3,0,0),30,None)]
        for angle in np.arange(8)*np.pi/4:
            tasks.append((base,design,(0.3,0,0),15,(float(angle),0.2)))
        for inertia in [0.05,0.10,0.16]:
            tasks.append(({**base,'name':'inertia_sensitivity','inertia':inertia},design,(0.3,0,0),15,None))
        for name,changes in [('torque60pct',{'torque':35.785372}),('mu_0.3',{'mu':0.3}),
                             ('36V',{'no_load':8.0115,'corner':5.8}),('mass33kg',{'mass':33})]:
            tasks.append(({**base,'name':name,**changes},design,(0.3,0,0),15,None))
        for command in [(0,0,0),(0.2,0,0),(0.4,0,0),(0,0.1,0),(0,0,0.3)]:
            tasks.append(({**base,'name':'commands'},design,command,20,None))
        for seed in range(8):
            rng=np.random.default_rng(seed)
            d={**design,'front_k':26*rng.uniform(.9,1.1),'back_k':29*rng.uniform(.9,1.1),
               'front_c':380*rng.uniform(.8,1.2),'back_c':410*rng.uniform(.8,1.2),
               'front_preload':rng.uniform(.5,1.5),'back_preload':rng.uniform(1,2)}
            tasks.append(({**base,'name':'tolerances','seed':seed,'inertia':float(rng.uniform(.05,.25))},d,(.3,0,0),20,None))
        for case,d,cmd,seconds,push in tasks:
            r=trial(d,case,cmd,seconds,push)
            rows.append(r)
            (OUT/'validation.json').write_text(json.dumps(rows,indent=2))
            print(len(rows),case['name'],cmd,push,json.dumps(compact(r)),flush=True)
        return
    if args.mode=='adverse':
        for name,offsets in [('existing',(42,46)),('medium',(20,20)),('cad',(0,0)),('short',(-20,-20))]:
            design={**POL.design,'thigh_rod':offsets[0],'shin_rod':offsets[1]}
            for stroke in [24,30]:
                d={**design,'front_preload':4-(stroke-24)/2,'back_preload':4.5-(stroke-24)/2}
                case={'name':'compound_adverse','mass':30,'inertia':0.25,'friction':1.2,'damping':0.4,'mu':0.4,'stroke':stroke}
                for push in [None,(np.pi/2,0.2)]:
                    r=trial(d,case,seconds=15,push=push)
                    r['design_name']=name
                    rows.append(r)
                    (OUT/'adverse.json').write_text(json.dumps(rows,indent=2))
                    print(name,stroke,push,json.dumps(compact(r)),flush=True)
        return
    for name,offsets in [('existing',(42,46)),('medium',(20,20)),('cad',(0,0)),('short',(-20,-20))]:
        design={**POL.design,'thigh_rod':offsets[0],'shin_rod':offsets[1]}
        cases=[{'name':'derated','mass':30}]
        if name=='existing':
            cases=[{'name':'legacy','mass':27.056989,'legacy':True},
                   {'name':'export_positive_assumptions','mass':30,'torque':59.642287,'no_load':10.68203,'corner':8.79916},*cases]
        for case in cases:
            t=time.time()
            r=trial(design,case)
            r['design_name']=name
            rows.append(r)
            (OUT/'screen.json').write_text(json.dumps(rows,indent=2))
            print(name,case['name'],json.dumps(compact(r)),'wall_seconds',round(time.time()-t,1),flush=True)

if __name__=='__main__': main()
