"""Offline controller rehearsal: cloud/GPU actions replaced, archive IO real."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import unittest

HERE=Path(__file__).resolve().parent

def load(name,file):
    spec=importlib.util.spec_from_file_location(name,HERE/file)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

controller=load('local_controller','run-single-host.py')
worker=load('local_worker','single-host-worker.py')

class SingleHostTest(unittest.TestCase):
    def test_manifest_shared_access_and_no_second_node(self):
        doc=controller.manifest('test-node')
        pods=[x for x in doc['items'] if x['kind']=='Pod']
        self.assertEqual(len(pods),1)
        engines,sidecar=pods[0]['spec']['containers']
        self.assertEqual(engines['resources']['limits']['nvidia.com/gpu'],'2')
        self.assertNotIn('nvidia.com/gpu',sidecar['resources']['limits'])
        self.assertEqual(pods[0]['spec']['nodeSelector'],{'kubernetes.io/hostname':'test-node'})
        self.assertNotIn('hostIPC',pods[0]['spec'])
        self.assertIn('single-host-worker.py',doc['items'][1]['data'])

    def test_transport_evidence_does_not_accept_available_lane(self):
        header='| remote memory read by ucp_get into cuda/GPU0 from cuda/dev[0] |\n'
        self.assertTrue(worker.ipc_read_tables(header+'| 1..inf | zero-copy | cuda_ipc/cuda |'))
        self.assertFalse(worker.ipc_read_tables('Available cuda_ipc/cuda lanes'))
        self.assertFalse(worker.ipc_read_tables(header+'| 1..inf | zero-copy | rc_mlx5/mlx5_0 |\nAvailable cuda_ipc/cuda'))

    def replay(self, failure=False, missing_gpu_once=False, unhealthy=False, eof_once=False):
        with tempfile.TemporaryDirectory() as directory:
            run=Path(directory)
            (run/'session.json').write_text(json.dumps({'profile':'nvlink-h200-local',
                'terraform_dir':'/not-a-real-root','cleanup_start_deadline_unix':time.time()+600}))
            (run/'apply-result.json').write_text('{"exit_code":0}')
            data=io.BytesIO()
            name='worker-failure.json' if failure else 'worker-result.json'
            with tarfile.open(fileobj=data,mode='w:gz') as archive:
                payload=b'{"offline_fixture":true}'
                item=tarfile.TarInfo(name);item.size=len(payload)
                archive.addfile(item,io.BytesIO(payload))
            calls=[];node_calls=0
            def rpc(cmd,**kwargs):
                nonlocal node_calls
                calls.append(cmd)
                self.assertNotIn('delete',cmd)
                if cmd[0]=='terraform':
                    text=json.dumps({'cluster_id':{'value':'cluster'},'node_group_ids':{'value':{'local':'group','cpu':None,'remote':None}}})
                elif 'get' in cmd and 'nodes' in cmd:
                    node_calls+=1
                    if eof_once and node_calls == 1:
                        return subprocess.CompletedProcess(cmd,1,'','unexpected EOF')
                    text=json.dumps({'items':[{'metadata':{'labels':{'nebius.com/node-group-id':'group','kubernetes.io/hostname':'node'}},'status':{'conditions':[{'type':'Ready','status':'True'},{'type':'NebiusGPUError','status':'True' if unhealthy else 'False'}],'allocatable':{'nvidia.com/gpu':'0' if missing_gpu_once and node_calls==1 else '8'}}}]})
                elif 'tar' in cmd:
                    return subprocess.CompletedProcess(cmd,0,data.getvalue(),b'')
                elif '/results/status.json' in cmd:
                    text=json.dumps({'phase':'failed-evidence-ready' if failure else 'evidence-ready'})
                elif 'apply' in cmd:
                    manifest=json.loads(kwargs['input'])
                    self.assertEqual(manifest['items'][-1]['spec']['containers'][0]['resources']['limits']['nvidia.com/gpu'],'2')
                    text='applied'
                elif 'get' in cmd and 'pod' in cmd:
                    text='{}'
                else:text=''
                return subprocess.CompletedProcess(cmd,0,text,'')
            c=controller.Controller(run,rpc=rpc,sleeper=lambda seconds:None)
            if unhealthy:
                with self.assertRaisesRegex(RuntimeError, 'Provider node health failed'):
                    c.execute()
                self.assertFalse(any('apply' in cmd for cmd in calls))
                return
            result=c.execute()
            self.assertTrue(result['saved_off_box'])
            self.assertEqual(result['worker_succeeded'],not failure)
            self.assertTrue(result['requires_nvlink_counter_review'])
            self.assertTrue((c.out/'evidence'/name).is_file())
            self.assertTrue(any('logs' in cmd for cmd in calls))
            if missing_gpu_once or eof_once:self.assertEqual(node_calls,2)

    def test_transient_status_read_retried(self):self.replay(eof_once=True)
    def test_unhealthy_node_rejected_before_deployment(self):self.replay(unhealthy=True)
    def test_success_collects_before_review(self):self.replay()
    def test_failure_also_preserves_evidence(self):self.replay(failure=True)
    def test_wait_for_gpu_advertisement(self):self.replay(missing_gpu_once=True)

if __name__=='__main__':unittest.main()

class WorkerFlowTest(unittest.TestCase):
    def replay_worker(self, wrong_transport=False, mismatch=False):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory); count=[0]; stages=[]; processes=[]
            table='| remote memory read into cuda/GPU0 from cuda/dev[0] |\n| 1..inf | zero-copy | cuda_ipc/cuda |\n'
            class Process:
                def __init__(self,cmd,stdout,**kwargs):
                    self.cmd=cmd;self.returncode=None;self.stdout=stdout;processes.append(self)
                    stages.append(cmd)
                    if 'vllm' in cmd or 'consumer' in cmd:
                        stdout.write(table.replace('cuda_ipc','tcp') if wrong_transport else table);stdout.flush()
                def poll(self):return self.returncode
                def wait(self,timeout=None):self.returncode=0;return 0
                def terminate(self):self.returncode=0
                def kill(self):self.returncode=-9
            def run(cmd,**kwargs):
                if 'inspect' in cmd:
                    gpu=cmd[cmd.index('--device')+1]
                    Path(cmd[cmd.index('--result')+1]).write_text(json.dumps({'selected_gpu_uuid':'GPU-'+gpu}))
                output='1, GPU-0, engine\n2, GPU-1, engine\n' if any('query-compute' in x for x in cmd) else 'fixture'
                return subprocess.CompletedProcess(cmd,0,output,'')
            class Response:
                status=200
                def __init__(self,body):self.body=body
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def read(self):return self.body.encode()
            def urlopen(request,**kwargs):
                if isinstance(request,str):
                    if request.endswith('/metrics'):
                        return Response('\n'.join([worker.COUNT+' '+str(count[0]),worker.SIZE+' '+str(count[0]*1000)]+[x+' 0' for x in worker.FAILURES]))
                    return Response('{}')
                body=json.loads(request.data);pd=':8000/' in request.full_url
                if pd:count[0]+=1
                return Response(json.dumps({'choices':[{'text':'bad' if pd and mismatch else 'answer'}],
                    'usage':{'prompt_tokens':len(body['prompt']),'completion_tokens':8}}))
            def prepare(path):
                path.mkdir();(path/'suite.json').write_text(json.dumps({'cases':[
                    {'case_id':'case-'+str(i),'input_tokens':2,'gold_token_ids':[1]*8,'gold_text':'answer',
                     'request_body':{'model':worker.MODEL,'prompt':[1,2],'max_tokens':8}} for i in range(8)]}))
            with patch.object(worker.subprocess,'run',run),patch.object(worker.subprocess,'Popen',Process),\
                 patch.object(worker.urllib.request,'urlopen',urlopen),patch.object(worker,'prepare_suite',prepare),\
                 patch.object(worker.time,'sleep',lambda x:None):
                worker.main(['--execute','--out',str(out)])
            self.assertTrue((out/'DONE').exists())
            self.assertTrue(all(p.poll() is not None for p in processes))
            if wrong_transport or mismatch:
                self.assertFalse((out/'worker-result.json').exists())
                self.assertTrue((out/'worker-failure.json').exists())
                if wrong_transport:self.assertFalse(any('vllm' in cmd for cmd in stages))
            else:
                result=json.loads((out/'worker-result.json').read_text())
                self.assertEqual(result['requests'],16)
                self.assertTrue(result['nvlink_hardware_counters_require_review'])
                self.assertFalse((out/'worker-failure.json').exists())
    def test_full_worker_sequence(self):self.replay_worker()
    def test_wrong_raw_transport_stops_before_models(self):self.replay_worker(wrong_transport=True)
    def test_output_mismatch_preserves_failure(self):self.replay_worker(mismatch=True)
