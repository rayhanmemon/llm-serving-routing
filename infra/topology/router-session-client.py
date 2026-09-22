#!/usr/bin/env python3
"""Execute one bounded native inference-perf trial on the CPU client Pod."""
import argparse,gzip,importlib.util,json,os,signal,subprocess,sys,time,threading
from pathlib import Path
HERE=Path(__file__).resolve().parent

def module(name):
    sp=importlib.util.spec_from_file_location(name,HERE/(name+'.py'));m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m
p=module('router-session-plan');results=module('router-session-results')

def execute(plan,suite,trial,out,url,metrics,pins,deadline,hosts=None):
    if time.time()+trial['max_seconds']+120>=deadline:raise TimeoutError('Trial would consume collection reserve')
    root=out/trial['id'];doc=p.make_trial(plan,trial,suite['cases'],root,url,pins)
    if hosts:
        c=module('tp4-client');c.write(root/'metrics-before.json',c.idle(hosts,deadline));c.write(root/'workers-before.json',c.evidence(hosts))
    stop=threading.Event();samples=[]
    def sample_load():
        while not stop.is_set():
            try:
                snap=c.snapshot(hosts)
                samples.append({'unix':time.time(),'workers':{role:{'running':v['values'][c.RUNNING],'waiting':v['values'][c.WAITING]} for role,v in snap.items()}})
            except Exception as error:samples.append({'unix':time.time(),'error':type(error).__name__})
            c.write(root/'engine-load.json',samples)
            stop.wait(.5)
    observer=threading.Thread(target=sample_load,daemon=True) if hosts else None
    if observer:observer.start()
    with (root/'native.log').open('w') as log:
        child=subprocess.Popen([sys.executable,str(HERE/'router-perf-adapter.py'),'--config_file',str(root/'config.yaml')],
            env={**os.environ,'ROUTER_METRICS_URL':metrics,'PYTHONPATH':'/workspace:/probe'},stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:code=child.wait(timeout=trial['max_seconds'])
        except BaseException:
            os.killpg(child.pid,signal.SIGINT)
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=5)
            (root/'failure.json').write_text(json.dumps({'reason':'interrupted or trial duration exceeded; partial records retained'}));raise
        finally:
            stop.set()
            if observer:observer.join(timeout=1)
    if code:raise RuntimeError('Native benchmark failed; inspect native.log')
    _,rows=results.records(root)
    if hosts:
        before=json.loads((root/'metrics-before.json').read_text())
        c.idle(hosts,deadline)
        for _ in range(20):
            after=c.snapshot(hosts)
            observed=sum(after[role]['values'][c.e.COUNT]-before[role]['values'][c.e.COUNT] for role in hosts)
            if observed==4*len(rows):break
            time.sleep(.5)
        else:raise ValueError('Completed requests did not produce the expected rank transfers')
        c.write(root/'metrics-after.json',after)
        final=c.evidence(hosts);c.stable(json.loads((root/'workers-before.json').read_text()),final);c.write(root/'workers-after.json',final)
    (root/'complete.json').write_text(json.dumps({'requests':len(rows),'foreground':sum(r['kind']=='foreground' for r in rows),
        'records':rows,'scope':'native inference-perf; pre-request counts sampled before client timing'}))

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--plan',type=Path,required=True);a.add_argument('--suite',type=Path,required=True)
    a.add_argument('--hosts');a.add_argument('--trial',required=True);a.add_argument('--out',type=Path,default=Path('/results/trials'))
    a.add_argument('--url',required=True);a.add_argument('--metrics',required=True);a.add_argument('--pins',required=True);a.add_argument('--deadline',type=float,required=True)
    x=a.parse_args();execute(json.loads(x.plan.read_text()),json.loads(gzip.decompress(x.suite.read_bytes())),json.loads(x.trial),x.out,x.url,x.metrics,json.loads(x.pins),x.deadline,json.loads(x.hosts) if x.hosts else None)
