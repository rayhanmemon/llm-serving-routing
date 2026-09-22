import copy
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
s=module('run-layout-local');l=module('layout-client');fixtures=module('test_pilot_session')

class LayoutTests(unittest.TestCase):
    def test_identity_access_probe_fails_before_infrastructure(self):
        with self.assertRaisesRegex(ValueError,'no infrastructure'):
            s.verify_cleanup_identity(lambda *a,**k:subprocess.CompletedProcess([],1,'','denied'))
        record=s.verify_cleanup_identity(lambda *a,**k:subprocess.CompletedProcess([],0,'{}',''))
        self.assertTrue(record['cleanup_identity_access_verified'])
        self.assertEqual(record['service_account_id'],s.pilot.LAYOUT_GUARD_ID)

    def plan(self):
        p=fixtures.PilotSessionTest().plan_json(profile=s.pilot.LAYOUT_PROFILE)
        p['variables'].update(cloud_guard={'value':True},single_gpu_host={'value':True},existing_guard_service_account_id={'value':s.pilot.LAYOUT_GUARD_ID})
        cpu=next(x['change']['after'] for x in p['resource_changes'] if x['address']=='nebius_mk8s_v1_node_group.cpu[0]')
        cpu['template']['service_account_id']=s.pilot.LAYOUT_GUARD_ID
        return p
    def test_admission_allows_only_one_gpu_host_and_existing_identity(self):
        p=self.plan();p["variables"]["single_gpu_host"]["value"]="true";s.pilot.validate_plan_structure(p,s.pilot.LAYOUT_PROFILE)
        p['resource_changes'].append({'address':'nebius_mk8s_v1_node_group.remote[0]','change':{'actions':['create']}})
        with self.assertRaises(s.pilot.SessionError):s.pilot.validate_plan_structure(p,s.pilot.LAYOUT_PROFILE)
    def test_rejects_wrong_cleanup_identity_or_missing_guard(self):
        for field,value in [('existing_guard_service_account_id','wrong'),('single_gpu_host',False),('cloud_guard',False)]:
            p=self.plan();p['variables'][field]['value']=value
            with self.assertRaises(s.pilot.SessionError):s.pilot.validate_plan_structure(p,s.pilot.LAYOUT_PROFILE)
    def test_packing_changes_only_extra_config_in_each_command(self):
        base,_=s.s.tp4.load_config();packed=dict(base,enable_cross_layers_blocks=True)
        for role in ('local','remote'):
            for a,b in zip(s.s.tp4.engine_specs(base,role,'ip'),s.s.tp4.engine_specs(packed,role,'ip')):
                i=a['command'].index('--kv-transfer-config')+1
                ak=json.loads(a['command'][i]);bk=json.loads(b['command'][i])
                self.assertTrue(bk['kv_connector_extra_config'].pop('enable_cross_layers_blocks'))
                self.assertEqual(ak,bk);b['command'][i]=a['command'][i];self.assertEqual(a,b)
    def test_manifest_keeps_one_pod_with_restart_wrapper_and_evidence(self):
        doc=s.manifest(s.s.tp4.DEFAULT,{'local':'node'})
        pods=[x for x in doc['items'] if x['kind']=='Pod'];self.assertEqual(len(pods),1)
        self.assertIn('/probe/layout-engines.py',pods[0]['spec']['containers'][0]['command'])
        self.assertIn('layout-client.py',doc['items'][1]['data'])
    def test_metric_delta_rejects_partial_rank_flush(self):
        a={m+suffix:0 for m in l.METRICS for suffix in ('_count','_sum')};b={m+suffix:4 for m in l.METRICS for suffix in ('_count','_sum')}
        row={'before':{'local':{'values':a}},'after':{'local':{'values':b}}};l.metric_delta(row)
        b[l.METRICS[0]+'_count']=3
        with self.assertRaises(ValueError):l.metric_delta(row)
    def check_sequence(self,reproduced):
        with tempfile.TemporaryDirectory() as directory:
            obj=object.__new__(s.Controller);obj.run=Path(directory);obj.k=['kubectl'];obj.cpu_node='cpu';obj.nodes_by_role={'local':'gpu'}
            obj.config_path=s.s.tp4.DEFAULT;obj.session={'cleanup_start_deadline_unix':time.time()+5000};events=[]
            obj.allocate=lambda role:events.append('allocate-'+role)
            obj.apply=lambda *a:None
            obj.call=lambda cmd,*a,**k:events.append('advance' if 'touch' in cmd else 'wait') or subprocess.CompletedProcess(cmd,0,'','')
            obj.ready=lambda name:events.append('ready-'+name)
            obj.epoch=lambda name:events.append(name) or {'default_slow_reproduced':reproduced}
            obj.collect=lambda:events.append('collect')
            with patch.object(s.s.paired.Controller,'bootstrap',side_effect=lambda *a,**k:events.append('guard')):
                obj.execute(HERE.parent.parent/'workloads/tp4-locality/code-suite/suite.json.gz')
            self.assertNotIn('allocate-remote',events)
            self.assertEqual(events.count('advance'),2 if reproduced else 0)
            self.assertEqual('default-b' in events,reproduced)
    def test_no_packed_restart_without_slow_baseline(self):self.check_sequence(False)
    def test_all_epochs_follow_guard_and_one_host(self):self.check_sequence(True)
    def test_real_constructor_accepts_layout_profile_without_cloud_calls(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d);policy=s.pilot.PROFILE_POLICIES[s.pilot.LAYOUT_PROFILE]
            session={'profile':s.pilot.LAYOUT_PROFILE,'terraform_dir':str(s.pilot.RDMA_TERRAFORM_DIR),
                     'terraform_plan_path':str(run/'plan'),'project_id':policy['project_id'],
                     'subnet_id':policy['subnet_id'],'infiniband_fabric':policy['infiniband_fabric'],
                     'cleanup_start_deadline_unix':time.time()+5000}
            (run/'session.json').write_text(json.dumps(session))
            with patch.object(s.s.paired.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{"variables":{},"configuration":{}}','')):
                obj=s.Controller(run,s.s.tp4.DEFAULT)
            self.assertEqual(obj.env['TF_VAR_single_gpu_host'],'true')
            self.assertEqual(obj.env['TF_VAR_existing_guard_service_account_id'],s.pilot.LAYOUT_GUARD_ID)
    def test_budget_covers_delete_deadline(self):
        p=s.pilot.PROFILE_POLICIES[s.pilot.LAYOUT_PROFILE]
        self.assertLess(float(p['hourly_rate_usd_pretax'])*p['deletion_target_seconds']/3600,float(p['attempt_admission_usd_pretax']))
