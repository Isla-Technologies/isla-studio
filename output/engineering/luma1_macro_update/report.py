"""Create an auditable HTML report from completed simulations, without inventing results."""
from search import OUT,inputs
import json,hashlib,html,math

def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def main():
    selected=read('selected.json'); c=selected['candidate'];d=c['design'];v=inputs()
    rows=read('validation.json');summary=read('validation_summary.json');base=read('baseline_summary.json')
    before=read('before/hardware.json');after=read('published/luma_1/hardware.json');evaluation=read('evaluation.json')
    pool=read('coarse.json')+read('refine.json');unique=len({json.dumps(r['candidate'],sort_keys=True) for r in pool})
    peakforce={p:max(s['force_peak'] for r in rows for s in r['shocks'] if s['position']==p) for p in ['front','back']}
    shaft=max(s['shaft_speed_peak'] for r in rows for s in r['shocks'])
    tilt=max(r['max_tilt_deg'] for r in rows);seconds=sum(r['simulated_seconds'] for r in rows)
    adverse=[r for r in rows if r['case']['name']=='adverse'];w=evaluation['walking']
    table=[]
    def add(label,old,new):table.append([label,str(old),str(new)])
    add('All-up simulation mass','25.56 kg',f"{after['mass']} kg (assumed)")
    add('Macro output torque limit','66 N·m',f"{v['peak_torque']:.2f} N·m calculated peak")
    add('Macro torque constant','3 N·m/A',f"{v['torque_constant']:.6f} N·m/A")
    add('Macro gear ratio','36:1',f"{v['gear_ratio']:.5f}:1")
    add('Macro no-load speed','5.8 rad/s assumed',f"{v['no_load_speed']:.3f} rad/s at 48 V")
    add('Macro corner speed','4.9 rad/s assumed',f"{v['corner_speed']:.3f} rad/s calculated")
    add('Macro continuous rating','22 A / 66 N·m stated','Not measured; 20 A is peak test current')
    for old,new in zip(before['rods'],after['rods']):
        add(f"{new['name'].title()} rod cut length (4 pieces)",f"{old['length_mm']:g} mm",f"{new['length_mm']:g} mm")
        add(f"{new['name'].title()} joint-axis spacing",f"{old['joint_to_joint_mm']:g} mm",f"{new['joint_to_joint_mm']:g} mm")
    for old,new in zip(before['ankles']['shocks'],after['ankles']['shocks']):
        p=new['position'].replace('back','rear').title()
        for label,key,unit in [('spring rate','rate_n_per_mm','N/mm'),('damping','damping','N·s/m'),('preload at full extension','preload_mm','mm'),('usable stroke','stroke_mm','mm')]:
            add(p+' '+label,f"{old[key]:g} {unit}",f"{new[key]:g} {unit}")
        add(p+' eye-to-eye: extended / compressed',f"{old['eye_to_eye_mm'][0]:g} / {old['eye_to_eye_mm'][1]:g} mm",f"{new['eye_to_eye_mm'][0]:g} / {new['eye_to_eye_mm'][1]:g} mm")
        add(p+' nominal model standing spring load',f"{old['standing_load']:g} N",f"{new['standing_load']:g} N")
    add('Demo command: forward / reverse','+0.60 / −0.30 m/s','+0.40 / −0.15 m/s')
    add('Demo command: sideways / turn','±0.25 m/s / ±0.80 rad/s','±0.10 m/s / ±0.30 rad/s')
    add('Neural policy weights','Original trained walking policy','Same weights; hardware searched and revalidated')
    comparison=[['Falls in matched 18-case retest',f"{base['falls']}/18",f"{selected['falls']}/18"],
                ['Minimum shock-stop clearance',f"{base['minimum_clearance']:.2f} mm",f"{selected['minimum_clearance']:.2f} mm"],
                ['Largest leg-motor trial RMS torque',f"{base['rms']:.2f} N·m",f"{selected['rms']:.2f} N·m"],
                ['Largest leg-motor peak torque',f"{base['peak']:.2f} N·m",f"{selected['peak']:.2f} N·m"],
                ['Largest straight-forward mean speed error',f"{base['speed_error']:.3f} m/s",f"{selected['speed_error']:.3f} m/s"]]
    def tbl(data,heads=('Parameter','Before: live demo','After: selected prototype')):
        return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+html.escape(x)+'</th>' for x in heads)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(x)+'</td>' for x in row)+'</tr>' for row in data)+'</tbody></table></div>'
    forces=[]
    for shock in after['ankles']['shocks']:
        p=shock['position'];rate=shock['rate_n_per_mm'];pre=shock['preload_mm'];stroke=shock['stroke_mm'];damper=shock['damping']
        compressed=rate*(stroke+pre);designforce=math.ceil(1.5*(compressed+damper*max(.5,shaft))/100)*100
        forces.append([p.replace('back','rear').title(),f"{peakforce[p]:.1f} N",f"{rate*pre:.1f} N",f"{compressed:.1f} N",f"{damper*.1:.1f} N",f"{designforce:g} N"])
    digest=hashlib.sha256((OUT/'macro_original.json').read_bytes()).hexdigest()
    shovefalls=sum(not held for mode in w['shoves'].values() for strength in mode['held'] for held in strength)
    body=f'''<p class="eyebrow">ISLA TECHNOLOGIES · 9 NOVEMBER 2026 · SIMULATION DESIGN REVIEW</p>
<h1>Luma-1 rods and passive ankle shocks</h1>
<p class="lead">Selected prototype: <strong>{200+d['thigh_rod']:g} mm thigh rods, {110+d['shin_rod']:g} mm shin rods, {c['stroke']:g} mm usable shock stroke</strong>. This is the best-scoring design that passed the prescribed checks in this finite search, not a proven global optimum or a production-qualified robot.</p>
<p><a href="/">Open the updated live demo</a> · <a href="engineering-results.json">Download calculation and test summary</a> · <a href="validation.json">All 56 validation trials</a></p>
<h2>Before and after</h2><p>Before means the configuration actually published on isla-demo before this update. Rod cut lengths include the portions entering their clamps; they are not joint-axis distances.</p>{tbl(table)}
<h2>What the Macro file supports</h2>
<p>The attachment is dated 8 November 2026, tested at 48 V with a 20 A peak current setting. Peak output torque = 2.982114349 × 20 = <strong>59.6423 N·m</strong>, 9.63% below the former 66 N·m simulation limit. This is a calculated peak, not a measured continuous torque rating. The export summary gives 10.682 rad/s no-load speed; its direct speed-test record gives 10.493 rad/s. The model uses the summary value and reduced-speed sensitivity cases.</p>
<p>The negative reflected inertia (−0.16098 kg·m²), negative Coulomb friction (−0.81061 N·m) and friction-fit R² of −0.5066 are invalid physical calibration results. Their signs were not silently corrected. Nominal simulation assumptions are 0.16 kg·m² reflected inertia, 0.81 N·m friction and 0.25 N·m·s/rad damping. Inertia was tested over 0.05–0.25 kg·m², with adverse friction 1.2 N·m and damping 0.4 N·m·s/rad. Continuous current/torque, backlash and drivetrain stiffness remain unmeasured. Micro actuator assumptions were retained.</p>
<h2>Search and simulation method</h2>
<p>{len(pool)} candidate evaluations ({unique} distinct configurations): a coarse grid/random search followed by local refinement. Each candidate ran two 9-second trials with the original policy. Variables were both rod lengths, front/rear spring rates, damping, preload and stroke. Feasibility required no fall or gantry support, commanded 0.30 m/s producing 0.22–0.38 m/s, lateral drift ≤0.06 m/s, at least 2 mm stop clearance, tilt ≤20°, and ≤2% leg-motor saturation. Among feasible candidates, the score minimized maximum trial leg RMS torque + 0.12×peak torque + 20×speed error + 0.05×stroke(mm) + 0.002×combined rod length(mm). This weighted score is a design preference, not a physical law.</p>
<p>The top three distinct feasible candidates then ran 18 checks each. The selected design had no falls, at least 2 mm clearance and ≤0.10 m/s mean straight-forward speed error. Selection used the original screening score among those passing. Final validation ran {summary['trials']} trials totaling {seconds:.0f} simulated seconds, with {summary['falls']} falls, minimum stop clearance <strong>{summary['minimum_clearance']:.2f} mm</strong>, maximum torso tilt {tilt:.1f}°, peak leg torque {summary['peak']:.2f} N·m and largest single-trial leg RMS torque {summary['rms']:.2f} N·m.</p>
<p>Compound adverse condition: 30 kg, 44.73 N·m peak (75% of export), no-load speed scaled to 40 V, 6.5 rad/s corner, ground friction μ=0.4 and the high inertia/friction assumptions. Pushes were applied in eight directions at four gait times: 6 N·s impulse (60 N for 0.1 s), equivalent to Δv=0.2 m/s for 30 kg. Across these 32 trials, mean forward speed was {min(r['movement']['forward'] for r in adverse):.3f}–{max(r['movement']['forward'] for r in adverse):.3f} m/s at a 0.30 m/s command. Additional separate sensitivities covered 33 kg, 60% peak torque (35.79 N·m), μ=0.3, and eight randomized spring ±10%, damping ±20%, preload ±0.5 mm and initial-pose cases. Those extra extremes were not all combined simultaneously.</p>
<p>MuJoCo native version 3.15.0, timestep 2 ms; existing 50 Hz walking policy. The gantry was inactive throughout validation. Falls are detected at the model's tilt/height limits; surviving a short simulated trial does not establish indefinite physical stability.</p>
<h2>Matched hardware comparison</h2>
<p>Both columns below use the same latest Macro assumptions, 30 kg mass, original policy and identical 18 commands/disturbances. This separates the hardware change from the actuator-data correction.</p>{tbl(comparison,('Matched test result','Old rods and 24 mm shocks','Selected rods and 32 mm shocks'))}
<h2>Manufacturer prototype specification</h2>
<p>Four thigh rods and four shin rods, cut to the lengths above. Model outside diameter is 20 mm; the existing clamp interfaces must be retained. Cut-length target tolerance: ±0.5 mm, proposed for prototype manufacture, not tolerance-qualified by this simulation. Carbon-fibre wall thickness, layup, modulus, clamp insertion length, adhesive and end fittings are not established by the actuator file. Do not infer structural certification from the walking result.</p>
<p>Two front and two rear shocks. Eye-to-eye dimensions above are full extension / full compression, with 32 mm mechanical travel centered on the CAD mount length. Preload is spring compression at full extension, not standing sag. Specify adjustable preload and damping. The model uses linear, equal compression/rebound damping; the supplier must provide force–velocity curves over at least ±{max(.5,shaft):.2f} m/s, and verify packaging, articulation and coil-bind clearance.</p>
{tbl(forces,('Shock','Peak force in 56-case validation','Full-extension spring force','Full-compression spring force','Damper force at 0.1 m/s','Proposed axial proof-load target'))}
<p>Force model: F = k(p + x) + c·v during compression, where k is N/mm, p and x are mm, c is N·s/m and v is m/s. At rebound, the damping contribution reverses. Proof-load targets above are rounded up from 1.5×[k(p+stroke)+c×max(0.5, observed shaft speed)] and are proposed component acceptance loads, not measured capacities or a fatigue rating. Highest shaft speed within the 56-case validation was {shaft:.3f} m/s. Stronger, outside-envelope impacts are not covered by these component targets.</p>
<p>Structural demand check: at the full 59.64 N·m actuator peak, a pure bending couple carried by rods spaced 60 mm apart (thigh) implies 994 N axial force per rod; at 36 mm spacing (shin), 1,657 N. A preliminary factor of two gives 1.99 kN and 3.31 kN respectively, before adding axial weight/impact and clamp eccentricity. Tube buckling requires Pcr=π²EI/(KL)², with I=π(Do⁴−Di⁴)/64; Di, laminate E, end restraint K and clamp behaviour are unknown, so this report cannot close the strength check. Supplier drawing and fixture proof/fatigue testing remain necessary.</p>
<h2>Operating envelope and remaining verification</h2>
<p>Use a 0.30 m/s level-ground prototype target. The demo now limits forward/reverse commands to +0.40/−0.15 m/s, side speed to ±0.10 m/s and turn rate to ±0.30 rad/s. The demo and standard evaluator retain the original ground/sole friction coefficient of 1.0; the hardware search used 0.6 nominal and lower-friction adverse cases. Sideways tracking remains weak: approximately 0.03–0.04 m/s achieved for a 0.10 m/s command; zero-speed commands exhibit approximately 0.02–0.04 m/s drift. The fresh standard evaluator also tested a speed ladder, directional movements, 64 stronger shove trials (Δv 0.3–0.9 m/s) and 60 seconds of random commands. It recorded {shovefalls}/64 falls in the stronger shove tests and {w['roam']['falls']} roaming falls; full results remain visible in the demo. Some surviving strong-push trials also exceeded the soft simulated shock limit by as much as 4.45 mm; a real hard stop cannot supply that travel. Such trials do not qualify as acceptable physical recoveries and are outside the recommended envelope. Load extrema from the standard evaluator exclude fallen trials; the separate 56-trial validation records retain every trial.</p>
<p>These results support prototype geometry and procurement discussion today. They do not establish walking on slopes, stairs, uneven terrain or under arbitrary disturbances. The model omits verified flexible-tube/clamp behaviour, detailed self-collision, thermal endurance, battery regeneration limits and measured transmission backlash. No physical robot test or new policy training was performed. Confirm continuous torque by thermal endurance tests at the measured RMS loads, remeasure inertia/friction, and validate the assembled robot with a safety tether before a production release.</p>
<h2>Traceability</h2><p>Input SHA-256: <code>{digest}</code>. All candidate scores, scripts, shortlist results and validation records are saved in the local engineering output folder. This review supersedes the earlier 242/156 mm recommendation and its asymmetric shock-travel proposal.</p>
<p>Model conventions: <a href="https://mujoco.readthedocs.io/en/stable/XMLreference.html#body-joint">MuJoCo joint armature, damping and friction</a>. Torque/current convention: <a href="https://docs.odriverobotics.com/v/latest/manual/control.html">ODrive controller documentation</a>. These references explain conventions; they do not validate the supplied actuator measurements.</p>'''
    page='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Luma-1 Macro engineering review</title><style>body{font:16px/1.65 system-ui,sans-serif;color:#182326;background:#f5f6f3;margin:0}main{max-width:1100px;margin:auto;padding:48px 24px 80px}h1{font-size:40px;line-height:1.15}h2{margin-top:40px;font-size:25px}.lead{font-size:20px}.eyebrow{letter-spacing:.1em;font-size:12px;color:#526461}a{color:#006d5b}table{border-collapse:collapse;width:100%;font-size:14px;background:white}th,td{padding:12px 14px;text-align:left;border-bottom:1px solid #d8ded9}th{background:#e6ede7}td:first-child{font-weight:600}.scroll{overflow:auto}code{overflow-wrap:anywhere}@media print{main{padding:0}h2{break-after:avoid}tr{break-inside:avoid}body{font-size:11px}a{color:inherit}}</style></head><body><main>'+body+'</main></body></html>'
    target=OUT/'published/luma_1'
    (target/'engineering.html').write_text(page,encoding='utf-8');(OUT/'Engineering_Report.html').write_text(page,encoding='utf-8')
    result={'input_sha256':digest,'selected':selected,'validation':summary,'matched_baseline':base,'comparison':table,'matched_comparison':comparison,'shock_loads':forces,'candidate_evaluations':len(pool),'distinct_candidates':unique,'standard_shove_falls':shovefalls}
    (target/'engineering-results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    (target/'validation.json').write_text(json.dumps(rows,separators=(',',':')),encoding='utf-8')
    (OUT/'comparison.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
