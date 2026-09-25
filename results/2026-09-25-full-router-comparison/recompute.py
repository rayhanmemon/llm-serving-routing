#!/usr/bin/env python3
"""Recompute frozen tuning and the 24-cell report from compact saved evidence."""
import importlib.util,json,tarfile,tempfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=HERE.parent.parent
spec=importlib.util.spec_from_file_location('results',REPO/'infra/topology/router-session-results.py')
results=importlib.util.module_from_spec(spec);spec.loader.exec_module(results)
spec=importlib.util.spec_from_file_location('plan',REPO/'infra/topology/router-session-plan.py')
planning=importlib.util.module_from_spec(spec);spec.loader.exec_module(planning)
plan=json.loads((REPO/'workloads/router-session/plan.json').read_text())
with tempfile.TemporaryDirectory() as temp:
    with tarfile.open(HERE/'verified-trials.tar.gz') as archive:archive.extractall(temp,filter='data')
    root=Path(temp)/'trials'
    training=[]
    for trial in planning.calibration_trials(plan):
        folder=root/trial['id'];complete=json.loads((folder/'complete.json').read_text())
        assert json.loads((folder/'transfer-verified.json').read_text())['validated']
        assert len(json.loads((folder/'routes.json').read_text()))==len(complete['records'])
        training.append(({'trial':trial},complete['records']))
    selected=results.tune(training,plan)
    loads=[sample for trial in planning.calibration_trials(plan)
           for sample in results.engine_load_samples(root/trial['id']/'engine-load.json')]
    queued=results.decoder_queue_observed(loads,plan['decoder_max_num_seqs'])
    selected['real_decoder_queue_observed']=queued
    selected['tradeoff_observed'] &= queued
    frozen=json.loads((HERE/'frozen-tuning.json').read_text())
    assert selected==frozen,'Recomputed frozen tuning differs from saved choices'
    evaluated=[]
    for trial in plan['trials']:
        folder=root/trial['id'];complete=json.loads((folder/'complete.json').read_text())
        assert json.loads((folder/'transfer-verified.json').read_text())['validated']
        assert len(json.loads((folder/'routes.json').read_text()))==len(complete['records'])
        evaluated.append(({'trial':trial},complete['records']))
    reproduced=results.summarize(evaluated,selected,plan)
    recorded=json.loads((HERE/'comparison-summary.json').read_text())
    assert reproduced==recorded,'Recomputed comparison differs from saved report'
    assert recorded['complete'] and len(recorded['cells'])==24
    print(json.dumps({'calibration_episodes':len(training),'comparison_cells':len(recorded['cells']),
                      'reference':selected['reference'],'allowance':selected['parameters']['allowance'],
                      'tradeoff_observed':selected['tradeoff_observed'],
                      'engineering_criterion_met':recorded['engineering_criterion_met']}))
