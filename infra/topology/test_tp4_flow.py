import copy
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
def module(name):
    sp=importlib.util.spec_from_file_location(name,HERE/(name+'.py'));m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m
s=module('run-tp4-staged');c=module('tp4-client');w=module('tp4-workload')


class TP4FlowTests(unittest.TestCase):
    def test_frozen_workload_counts_and_orders(self):
        path=HERE.parent.parent/'workloads/tp4-locality/code-suite/suite.json.gz'
        plan=json.loads(gzip.decompress(path.read_bytes()))
        self.assertEqual(len(plan['cases']),8);self.assertEqual(len(plan['pairs']),48)
        for block in range(3):
            for length in [4096,32768,65536,122880]:
                rows=[r for r in plan['pairs'] if r['block']==block and r['input_tokens']==length]
                self.assertEqual(len(rows),4);self.assertEqual(sum(r['order'][0]=='local' for r in rows),2)
        self.assertEqual(plan['pairs'],w.timing_pairs(plan['cases']))
        for case in plan['cases']:
            self.assertEqual(len(case['request_body']['prompt']),case['input_tokens'])
            self.assertLess(case['input_tokens']+case['request_body']['max_tokens'],131072)

    def test_staged_plan_rejects_remote_in_local_phase(self):
        local='nebius_mk8s_v1_node_group.local';remote='nebius_mk8s_v1_node_group.remote[0]'
        original={'variables':{'v':1},'configuration':{'resources':'fixed'}}
        plan={**original,'resource_changes':[{'address':local,'change':{'actions':['create']}}]}
        s.validate_stage(original,plan,{local})
        for bad in [dict(plan,variables={}),dict(plan,configuration={}),
                    dict(plan,resource_changes=plan['resource_changes']+[{'address':remote,'change':{'actions':['create']}}])]:
            with self.assertRaises(ValueError):s.validate_stage(original,bad,{local})

    def test_renderer_preserves_frozen_config_bytes(self):
        doc=s.tp4.render(s.tp4.DEFAULT,{'local':'a'})
        self.assertEqual(doc['items'][1]['data']['config.json'],s.tp4.DEFAULT.read_text())

    def test_remote_is_blocked_without_actual_local_marker(self):
        with tempfile.TemporaryDirectory() as d:
            controller=object.__new__(s.Controller);controller.run=Path(d)
            with self.assertRaisesRegex(ValueError,'qualification'):controller.allocate('remote')

    def test_sequence_has_no_remote_before_local_qualification(self):
        controller=object.__new__(s.Controller);controller.k=['kubectl'];controller.tf=['terraform']
        controller.nodes_by_role={};controller.cpu_node='cpu-node';events=[]
        controller.call=lambda *a,**k:subprocess.CompletedProcess(a,0,json.dumps({'node_group_ids':{'value':{'cpu':'c'}}}),'')
        controller.nodes=lambda _: {'cpu':'cpu-node'}
        controller.allocate=lambda role:events.append('allocate-'+role)
        controller.deploy=lambda role:events.append('deploy-'+role)
        controller.apply=lambda *a:events.append('client')
        controller.phase=lambda phase:events.append('phase-'+phase)
        controller.collect=lambda:events.append('collect')
        suite=HERE.parent.parent/'workloads/tp4-locality/code-suite/suite.json.gz'
        with patch.object(s.paired.Controller,'bootstrap',side_effect=lambda **kwargs:events.append('guard')):
            controller.execute(suite)
        self.assertEqual(events,['guard','allocate-local','deploy-local','client','phase-local','collect',
                                  'allocate-remote','deploy-remote','phase-remote','collect','phase-timings','collect'])

    def test_client_data_fits_configmap_and_does_not_execute_by_itself(self):
        suite=HERE.parent.parent/'workloads/tp4-locality/code-suite/suite.json.gz'
        doc=s.client_manifest('cpu',suite)
        self.assertLess(len(json.dumps(doc['items'][0])),1024*1024)
        self.assertEqual(doc['items'][1]['spec']['containers'][0]['command'],['sleep','8000'])

    def test_quota_envelope_covers_worst_case_runtime(self):
        policy=s.pilot.PROFILE_POLICIES[s.PROFILE]
        self.assertLess(float(policy['hourly_rate_usd_pretax'])*policy['deletion_target_seconds']/3600,100)
        self.assertLess(policy['cleanup_start_seconds'],policy['deletion_target_seconds'])

    def test_targeted_allocation_uses_resources_not_incomplete_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            controller=object.__new__(s.Controller);controller.run=Path(directory)
            controller.tf=['terraform'];controller.nodes_by_role={}
            controller.session={'cleanup_start_deadline_unix':time.time()+4000}
            controller.original={'variables':{},'configuration':{}}
            target='nebius_mk8s_v1_node_group.local'
            changes=[{'address':a,'change':{'actions':['create']}} for a in [target,'nebius_compute_v1_gpu_cluster.local']]
            def call(cmd,name,*args,**kwargs):
                if 'plan' in cmd:
                    (controller.run/'local.tfplan').write_text('fixture')
                    value={}
                elif name=='local-plan-json':value={**controller.original,'resource_changes':changes}
                elif name=='local-state':value={'values':{'root_module':{'resources':[{'address':target,'values':{'id':'local-group'}}]}}}
                else:raise AssertionError(name)
                return subprocess.CompletedProcess(cmd,0,json.dumps(value),'')
            controller.call=call
            controller.nodes=lambda groups: {'local':'node-a'} if groups=={'local':'local-group'} else self.fail('wrong group')
            with patch.object(s.pilot,'apply_plan',return_value=0):controller.allocate('local')
            self.assertEqual(controller.nodes_by_role,{'local':'node-a'})


if __name__=='__main__':unittest.main()
