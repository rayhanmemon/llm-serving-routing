"""Run real controller methods; substitute only external commands and paid cleanup."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('layout_workflow',HERE/'run-layout-local.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
CFG=HERE.parent.parent/'workloads/tp4-v029/config.json'
EPOCHS=['default-a','packed-doc','packed','default-b']

def marker():
    return {'mode':'qualification','requests':4,'timed_requests':0,
            'parity':[{'matches':True},{'matches':True}],'transport':{'fixture':True}}

class WorkflowTests(unittest.TestCase):
    def replay(self,fault=None):
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp);events=[];completed=[];clock=[time.time()];stopped=[]
            session={'session_id':'fixture','profile':r.pilot.LAYOUT_PROFILE,
                'terraform_dir':'/no-cloud-terraform','terraform_plan_path':str(run/'original.tfplan'),
                'project_id':'p','subnet_id':'s','infiniband_fabric':'f',
                'cleanup_start_deadline_unix':clock[0]+5000}
            r.pilot.write_json(run/'session.json',session)
            plan={'variables':{},'configuration':{},'resource_changes':[
                {'address':x,'change':{'actions':['create']}} for x in
                ('nebius_mk8s_v1_node_group.local','nebius_compute_v1_gpu_cluster.local')]}
            def archive(pod):
                buf=io.BytesIO()
                with tarfile.open(fileobj=buf,mode='w:gz') as tar:
                    for epoch in completed:
                        files=({'complete.json':marker(),'requests.json':[{'response':{},'after':{}}]*4,
                                'final-evidence.json':{},'plan.json':{'mode':'qualification'}} if pod=='client'
                               else {'effective-config.json':{},'supervisor.log':'fixture'})
                        for name,value in files.items():
                            if fault=='missing-requests' and pod=='client' and name=='requests.json':continue
                            data=(value if isinstance(value,str) else json.dumps(value)).encode()
                            info=tarfile.TarInfo(epoch+'/'+name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
                return buf.getvalue()
            def rpc(cmd,**kw):
                events.append(cmd);text='';code=0
                if fault=='interrupt' and 'apply' in cmd:raise KeyboardInterrupt()
                if cmd[0]=='terraform' or cmd[0].endswith('/terraform'):
                    if 'show' in cmd:
                        text=json.dumps(plan if len(cmd)>4 else {'values':{'root_module':{'resources':[
                            {'address':a,'values':{'id':v}} for a,v in
                            [('nebius_mk8s_v1_cluster.topology','cluster'),('nebius_mk8s_v1_node_group.cpu[0]','cpu'),('nebius_mk8s_v1_node_group.local','local')]]}}})
                    elif 'plan' in cmd:Path(next(x[5:] for x in cmd if x.startswith('-out='))).write_text('fixture')
                elif 'nodes' in cmd:
                    text=json.dumps({'items':[{'metadata':{'labels':{'nebius.com/node-group-id':role,'kubernetes.io/hostname':role}},
                        'status':{'conditions':[{'type':'Ready','status':'True'},{'type':'NebiusGPUError','status':'False'}],
                                  'allocatable':{'nvidia.com/gpu':'8'}}} for role in ('cpu','local')]})
                elif 'logs' in cmd and 'deadline-guard' in cmd:
                    text=json.dumps({'armed':True,'cluster':'wrong' if fault=='guard' else 'cluster',
                        'deadline':session['cleanup_start_deadline_unix'],'gpu_groups_present_at_arm':[]})
                elif 'get' in cmd and 'pod' in cmd:
                    state={'podIP':'10.0.0.1','phase':'Running'}
                    if fault=='image':state['containerStatuses']=[{'state':{'waiting':{'reason':'ImagePullBackOff'}}}]
                    text=json.dumps({'status':state})
                elif 'tar' in cmd:
                    pod=cmd[cmd.index('exec')+1]
                    if fault=='archive-error' and pod=='local':return subprocess.CompletedProcess(cmd,1,b'',b'failed')
                    return subprocess.CompletedProcess(cmd,0,b'corrupt' if fault=='archive-corrupt' and pod=='local' else archive(pod),b'')
                elif 'cat' in cmd:
                    path=cmd[-1]
                    if path.endswith('.exit'):
                        epoch=Path(path).stem;completed.append(epoch)
                        text='1' if fault=='client' else '0'
                    elif path.endswith('/complete.json'):
                        value=marker()
                        if fault=='marker':value['requests']=3
                        text=json.dumps(value)
                    elif path.endswith('/shutdown.json'):
                        if fault=='shutdown-timeout':code=1
                        else:text=json.dumps({'drained':fault!='shutdown'})
                    else:code=1
                elif 'touch' in cmd:stopped.append(Path(cmd[-1]).stem)
                elif '-c' in cmd and 'python3' in cmd and fault=='startup':code=1
                elif 'sh' in cmd and '-c' in cmd:
                    script=cmd[-1]
                    if 'cat /results/layout-failure' in script:
                        if fault=='startup':text='{"error":"engine failed"}'
                        else:code=1
                return subprocess.CompletedProcess(cmd,code,text,'')
            def apply_gpu(*_):
                self.assertTrue((run/'cloud-guard-ready.json').exists())
                if fault=='manual-stop':(run/'manual-stop-requested.json').write_text('{}')
                return 1 if fault=='allocation' else 0
            def sleep(seconds):clock[0]+=seconds
            with patch.object(r.subprocess,'run',side_effect=rpc),patch.object(r.pilot,'apply_plan',side_effect=apply_gpu) as gpu, \
                 patch.object(r.pilot,'spawn_guard',return_value=type('Guard',(),{'pid':123})()), \
                 patch.object(r.pilot,'wait_guard_ready',side_effect=RuntimeError('guard startup') if fault=='desktop-guard' else None), \
                 patch.object(r.pilot,'cleanup_until_target',return_value=0) as cleanup, \
                 patch.object(r.time,'time',side_effect=lambda:clock[0]),patch.object(r.time,'sleep',side_effect=sleep):
                if fault:
                    with self.assertRaises(BaseException) as raised:r.run_admitted(run,session,CFG,CFG.with_name('suite.json.gz'),'qualification')
                    expected={'desktop-guard':'guard startup','guard':'Invalid cloud guard','allocation':'GPU placement','image':'container failed','startup':'Engine failure','client':'client failed','marker':'epoch result','archive-error':'collection incomplete','archive-corrupt':'collection incomplete','missing-requests':'collection incomplete','shutdown':'did not drain','shutdown-timeout':'acknowledgement missing','interrupt':'','manual-stop':'manual stop'}
                    self.assertIn(expected[fault],str(raised.exception))
                    self.assertTrue((run/'failure.json').exists())
                    self.assertFalse((run/'qualification-complete.json').exists())
                else:
                    r.run_admitted(run,session,CFG,CFG.with_name('suite.json.gz'),'qualification')
                    self.assertEqual(completed,EPOCHS);self.assertEqual(stopped,EPOCHS)
                    self.assertTrue((run/'qualification-complete.json').exists())
                    for epoch in EPOCHS:self.assertTrue((run/(epoch+'-shutdown.json')).exists())
                cleanup.assert_called_once_with(run)
                if fault in ('desktop-guard','guard','interrupt'):gpu.assert_not_called()
                if fault in ('archive-error','archive-corrupt','missing-requests','client','marker'):self.assertEqual(stopped,[])
                if fault=='image':self.assertFalse(any('nohup' in str(cmd) for cmd in events))
                if fault in ('archive-error','archive-corrupt'):
                    self.assertTrue((run/'paired/client/default-a/requests.json').exists())

    def test_real_controller_success_and_each_failure_always_cleanup(self):
        for fault in (None,'desktop-guard','guard','allocation','image','startup','client','marker',
                      'archive-error','archive-corrupt','missing-requests','shutdown','shutdown-timeout','interrupt','manual-stop'):
            with self.subTest(fault=fault):self.replay(fault)

    def test_http_failures_retain_partial_requests_without_success(self):
        spec=importlib.util.spec_from_file_location('http_replay',HERE/'rehearse-layout-client.py')
        http=importlib.util.module_from_spec(spec);spec.loader.exec_module(http)
        for fault in ('http-error','incomplete-stream'):
            with tempfile.TemporaryDirectory() as tmp,self.subTest(fault=fault):
                out=Path(tmp)
                with self.assertRaises(Exception):http.rehearsal(CFG.with_name('suite.json.gz'),CFG,out,'qualification',fault)
                self.assertTrue((out/'default-a/requests.json').exists())
                self.assertTrue((out/'default-a/failure.json').exists())
                self.assertFalse((out/'default-a/complete.json').exists())

if __name__=='__main__':unittest.main()
