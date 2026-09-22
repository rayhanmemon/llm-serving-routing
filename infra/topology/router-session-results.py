"""Validate native inference-perf records, tune on training only, summarize held-out blocks."""
import hashlib,itertools,json,math,statistics,uuid
from pathlib import Path

POLICIES=('none','hard','soft','absolute-cap','allowance')

def records(folder,require_counts=True):
    doc=json.loads((folder/'requests.json').read_text());rows=json.loads((folder/'reports/per_request_lifecycle_metrics.json').read_text())
    planned={str(uuid.UUID(bytes=uuid.uuid5(uuid.NAMESPACE_URL,doc['trial']['id']+'/'+str(r['index'])).bytes,version=4)):r for r in doc['requests']}
    result=[]
    if len(rows)!=len(planned):raise ValueError('Incomplete native benchmark report')
    seen=set()
    for row in rows:
        info=row.get('info',{});extra=info.get('extra_info',{});key=extra.get('request_key')
        if row.get('error') or key not in planned or key in seen:raise ValueError('Failed/duplicate/unmatched request')
        seen.add(key);p=planned[key];body=json.loads(row['request']);metrics=info['response_metrics'];usage=metrics['server_usage'];times=metrics['chunk_times']
        if body!=p['body'] or usage.get('prompt_tokens')!=len(body['prompt']) or usage.get('completion_tokens')!=body['max_tokens']:
            raise ValueError('Actual request/output differs from frozen workload')
        if '[DONE]' not in row['response'] or not times or times!=sorted(times):raise ValueError('Invalid stream')
        if not all(math.isfinite(v) for v in [row['start_time'],row['end_time'],*times]) or not row['start_time']<=times[0]<=times[-1]<=row['end_time']:
            raise ValueError('Invalid native timestamps')
        counts=extra.get('pre_request_counts',{})
        if require_counts and (extra.get('observation_error') or any(k not in counts for k in ('local','remote'))):
            raise ValueError('Router in-flight observation missing')
        result.append({'request_key':key,'kind':p['kind'],'index':p['index'],'pin':p['pin'],'case_id':p['case_id'],
                       'input_tokens':len(body['prompt']),'output_tokens':usage['completion_tokens'],
                       'ttft':times[0]-row['start_time'],'duration':row['end_time']-row['start_time'],
                       'maximum_chunk_gap':max([b-a for a,b in zip(times,times[1:])],default=0),
                       'counts':counts,'request_hash':hashlib.sha256(row['request'].encode()).hexdigest()})
    return doc,result

def choose(policy,params,local,remote):
    if policy=='hard':return ['local']
    if policy=='allowance' and local-remote<=params['allowance']:return ['local']
    if policy=='absolute-cap':
        eligible=[r for r,n in [('local',local),('remote',remote)] if n<=params['cap']]
        if 'local' in eligible:return ['local']
        if eligible:return eligible
    maximum=max(local,remote);idle=params.get('idle_threshold',0);busy=params.get('max_busy_score',1)
    score=lambda n:1 if n<=idle else ((maximum-n)/maximum*busy if maximum else 1)
    left=score(local)+(params.get('weight',0) if policy=='soft' else 0);right=score(remote)
    return ['local'] if left>right else ['remote'] if right>left else ['local','remote']

def decoder_queue_observed(samples,maximum):
    return any(v.get('running',0)>=maximum and v.get('waiting',0)>0
               for sample in samples for role,v in sample.get('workers',{}).items() if role in ('local','remote'))

def tune(training,plan):
    samples={}
    for doc,rows in training:
        for row in rows:
            if row['kind']!='foreground':continue
            key=(tuple(doc['trial']['state']),doc['trial']['repeat'],row['input_tokens'])
            samples.setdefault(key,{})[row['pin']]=row
    if len(samples)!=16 or any(set(x)!= {'local','remote'} for x in samples.values()):raise ValueError('Training pair matrix incomplete')
    candidates={'none':[{}],'hard':[{}],
        'allowance':[{'allowance':x} for x in plan['grid']['allowance']],
        'absolute-cap':[{'cap':x} for x in plan['grid']['cap']],
        'soft':[dict(zip(('weight','idle_threshold','max_busy_score'),x)) for x in itertools.product(*(plan['grid'][k] for k in ('weight','idle_threshold','max_busy_score')))]}
    scores={};selected={}
    for policy,grid in candidates.items():
        values=[]
        for params in grid:
            costs=[]
            for pair in samples.values():
                # Same recorded route costs train every candidate. Counts are sampled, not a causal simulator.
                for observed in pair.values():
                    c=observed['counts'];routes=choose(policy,params,c['local'],c['remote'])
                    costs.append(statistics.mean(pair[r]['ttft'] for r in routes))
            values.append({'parameters':params,'mean_route_cost':statistics.mean(costs)})
        values.sort(key=lambda x:(x['mean_route_cost'],json.dumps(x['parameters'],sort_keys=True)))
        scores[policy]=values;selected[policy]=values[0]['parameters']
    reference=min((p for p in POLICIES if p!='allowance'),key=lambda p:scores[p][0]['mean_route_cost'])
    grouped={}
    for (state,repeat,length),pair in samples.items():
        grouped.setdefault((state,length),[]).append(pair['remote']['ttft']-pair['local']['ttft'])
    # Average the reversed route orders BEFORE the crossover screen. Otherwise
    # serial/shared prefill timing alone could manufacture both signs.
    contrasts=[statistics.mean(values) for values in grouped.values()]
    pressure=max(row['counts']['local'] for pair in samples.values() for row in pair.values())>plan['decoder_max_num_seqs']
    # An observed tradeoff is necessary. This is a screen, not a significance test.
    return {'parameters':selected,'reference':reference,'candidate_scores':scores,
            'tradeoff_observed':pressure and max(contrasts)>=.05 and min(contrasts)<=-.05,
            'queue_pressure_signal_observed':pressure,'route_cost_contrasts_seconds':contrasts,'method':'Finite shared-data route-cost tuning; correlated probes and sampled pre-request counts. Validate actual policies only on held-out trials.'}

def summarize(trials,tuning,plan):
    cells={}
    for doc,rows in trials:
        t=doc['trial'];policy=t['policy'];policy=tuning['reference'] if policy=='reference' else policy
        key=(t['repeat'],t['trace'],policy)
        if key in cells:raise ValueError('Duplicate evaluation cell')
        foreground=[r for r in rows if r['kind']=='foreground'];background=[r for r in rows if r['kind']=='background']
        if len(foreground)!=4:raise ValueError('Incorrect foreground count')
        cells[key]={'mean_ttft':statistics.mean(r['ttft'] for r in foreground),
                    'mean_background_duration':statistics.mean(r['duration'] for r in background),
                    'max_background_chunk_gap':max(r['maximum_chunk_gap'] for r in background),
                    'foreground':len(foreground),'background':len(background)}
    expected={(t['repeat'],t['trace'],tuning['reference'] if t['policy']=='reference' else t['policy']) for t in plan['trials']}
    complete=set(cells)==expected;blocks=[];passes=complete
    for repeat in range(3):
        needed=[(repeat,tr,p) for tr in ('low','high') for p in ('allowance',tuning['reference'])]
        if not all(k in cells for k in needed):continue
        proposed=statistics.mean(cells[(repeat,tr,'allowance')]['mean_ttft'] for tr in ('low','high'))
        baseline=statistics.mean(cells[(repeat,tr,tuning['reference'])]['mean_ttft'] for tr in ('low','high'))
        gain=baseline-proposed;ok=gain>.05 and gain/baseline>.02
        for trace in ('low','high'):
            a=cells[(repeat,trace,'allowance')];b=cells[(repeat,trace,tuning['reference'])]
            for metric in ('mean_ttft','mean_background_duration','max_background_chunk_gap'):
                if a[metric]>b[metric]*1.05:ok=False
        blocks.append({'repeat':repeat,'mean_gain_seconds':gain,'gain_percent':gain/baseline*100,'passes':ok});passes &=ok
    # A second baseline cannot be ignored just because training selected another.
    for repeat in range(2):
        for trace in ('low','high'):
            if all((repeat,trace,p) in cells for p in POLICIES):
                a=cells[(repeat,trace,'allowance')]['mean_ttft']
                if a>min(cells[(repeat,trace,p)]['mean_ttft'] for p in POLICIES if p!='allowance')*1.05:passes=False
    return {'complete':complete,'engineering_criterion_met':passes,'blocks':blocks,
            'cells':[{'repeat':k[0],'trace':k[1],'policy':k[2],**v} for k,v in cells.items()],
            'claim_limit':'Observed controlled means in one deployment; no tail or broad no-regression claim.'}
