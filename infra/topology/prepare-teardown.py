#!/usr/bin/env python3
"""Remove drain blockers only in the experiment cluster recorded by Terraform."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

p = argparse.ArgumentParser()
p.add_argument('--terraform-dir', type=Path, required=True)
a = p.parse_args()
here = Path(__file__).resolve().parent
root = a.terraform_dir.resolve()
if root not in [(here / n).resolve() for n in ('terraform', 'terraform-rtx', 'terraform-rdma')]:
    raise SystemExit('Unknown experiment Terraform root')
state = json.loads(subprocess.check_output(['terraform', f'-chdir={root}', 'show', '-json'], timeout=30))
clusters = [x['values']['id'] for x in state.get('values', {}).get('root_module', {}).get('resources', [])
            if x.get('address') == 'nebius_mk8s_v1_cluster.topology' and x.get('values', {}).get('id')]
if not clusters:
    print('No experiment Kubernetes cluster remains in Terraform state.')
    raise SystemExit(0)
if len(clusters) != 1:
    raise SystemExit('Ambiguous experiment cluster')
with tempfile.TemporaryDirectory(prefix='router-cleanup-') as tmp:
    cfg = str(Path(tmp) / 'kubeconfig')
    subprocess.run([str(Path.home() / '.nebius/bin/nebius'), 'mk8s', 'cluster', 'get-credentials',
                    '--id', clusters[0], '--external', '--kubeconfig', cfg,
                    '--context-name', 'router-cleanup', '--force', '--no-browser',
                    '--timeout', '20s'], check=True, timeout=35, stdout=subprocess.DEVNULL)
    k = ['kubectl', '--kubeconfig', cfg, '--context', 'router-cleanup', '--request-timeout=15s', '-n', 'kube-system']
    rows = json.loads(subprocess.check_output(k + ['get', 'pdb', '-o', 'json'], timeout=20))
    names = [item['metadata']['name'] for item in rows.get('items', [])]
    if names:
        print('Removing experiment-cluster drain blockers:', ', '.join(names), flush=True)
        subprocess.run(k + ['delete', 'pdb', *names, '--ignore-not-found=true', '--wait=false'], check=True, timeout=25)
