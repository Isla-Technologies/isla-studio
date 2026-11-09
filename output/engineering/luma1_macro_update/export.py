"""Export the selected, revalidated hardware with the original policy weights."""
from search import OUT, ROOT, rr, cases, inputs
from pathlib import Path
from dataclasses import replace
from datetime import datetime, timezone
import sys,json,shutil,hashlib
from isla_rl.learn.trainer import Run
from isla_rl.learn.policy import Commands
from isla_rl.learn.evaluate import evaluate,describe
from isla_rl.sim.session import LiveSession

DEMO=ROOT.parent/'isla-demo'
sys.path.insert(0,str(DEMO/'scripts'))
import publish

def main():
    c=json.loads((OUT/'selected.json').read_text())['candidate']
    v=inputs(); d=c['design']; summary=json.loads((OUT/'validation_summary.json').read_text())
    if c.get('compression_extra_mm'):raise RuntimeError('Asymmetric travel requires explicit model export support')
    if summary['falls']:raise RuntimeError('Selected design failed validation; investigate before publishing')
    p=rr.make_profile(d,{**cases()[0],'stroke':c['stroke']})
    p.data['chosen_design']=d
    p.data['mass']['note']='Provisional 30 kg all-up test mass; target compensates changed rod mass added by builder. Actual assembled mass is not measured.'
    macro=p.data['actuator_types']['macro']
    macro.update(gear_ratio=v['gear_ratio'],torque_constant=v['torque_constant'],peak_current=20,
                 continuous_current=None,continuous_torque=None,assumed=['armature','frictionloss','damping'],
                 driver='ODrive V3.6 (firmware 0.5.x)',
                 note='2026-11-08 Macro export, 48 V. Peak torque calculated from Kt times 20 A; continuous rating unmeasured. Invalid negative inertia and friction fits replaced by explicit sensitivity assumptions.',
                 public_evidence_note='48 V bench export: 59.64 N·m is calculated peak torque at 20 A, not a continuous rating. Inertia/friction fits were invalid; positive simulation assumptions were sensitivity-tested.')
    allrows=sum([json.loads((OUT/f).read_text()) for f in ['coarse.json','refine.json']],[])
    unique=len({json.dumps(r['candidate'],sort_keys=True) for r in allrows})
    p.data['public_engineering_review']={
        'date':'9 November 2026','summary':f"Selected prototype: {200+d['thigh_rod']:g} mm thigh rods, {110+d['shin_rod']:g} mm shin rods, {c['stroke']:g} mm shock travel.",
        'scope':'Simulation selection at an assumed 30 kg. Existing policy revalidated without retraining. Level ground only; physical endurance and structural qualification remain outstanding.',
        'candidates':unique,'validation_trials':summary['trials'],'validation_falls':summary['falls'],
        'minimum_stroke_margin_mm':round(summary['minimum_clearance'],2),
        'report_url':'/robots/luma_1/engineering.html'}
    folder=ROOT/'runs/luma1-macro-20261109';folder.mkdir(parents=True,exist_ok=True)
    for name in ['setup.json','progress.csv']:shutil.copy2(ROOT/'examples/runs/luma1-walk'/name,folder/name)
    (folder/'robot.json').write_text(json.dumps({**p.data,'_folder':str(p.folder)},indent=2),encoding='utf-8')
    policy=replace(rr.POL,name='Luma-1 Macro hardware review 2026-11-09',design=d,
                   commands=Commands(forward=(-.15,.4),sideways=.1,turning=.3),
                   about='Original walking policy weights; hardware selected and revalidated with the November 8 Macro export. No new policy training. Conservative demo commands.',
                   created=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    (folder/'policy.json').write_text(policy.to_json(),encoding='utf-8')
    LiveSession._stands_alone=lambda self:False
    print('Running fresh standard evaluation...',flush=True)
    report=evaluate(policy,p)
    report['steps']=Run(folder).summary()['steps'];report['tested']=datetime.now(timezone.utc).isoformat(timespec='seconds')
    (folder/'evaluation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (OUT/'evaluation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(describe(report),flush=True)
    stage=OUT/'published';stage.mkdir(exist_ok=True)
    target=publish.publish(Run(folder),stage)
    training=json.loads((target/'training.json').read_text())
    training['provenance_note']='Training curves below belong to the original walking policy. This update searched hardware and revalidated the existing weights; it did not retrain the neural network.'
    publish._json(target/'training.json',training)
    (OUT/'selected_profile.json').write_text(json.dumps(p.data,indent=2),encoding='utf-8')
    print('STAGED',target,flush=True)

if __name__=='__main__':main()
