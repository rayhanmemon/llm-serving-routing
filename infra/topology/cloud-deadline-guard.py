#!/usr/bin/env python3
"""Cloud-side fallback: delete only this session's three named node groups.

Runs on the non-preemptible CPU node with its temporary service account.
Credentials come from instance metadata and never enter results or logs.
The normal controller still performs complete Terraform cleanup and verification.
"""
import argparse
import asyncio
import json
import math
from pathlib import Path
import time
import urllib.request

NAMES = {'router-local', 'router-remote', 'router-cpu'}


def shortened_deadline(current,request,cluster):
    value=request.get('deadline')
    if request.get('cluster')!=cluster or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0 or value>current:
        raise ValueError('Invalid deadline reduction')
    return value


def select_groups(items, cluster):
    result = {}
    for item in items:
        if item['parent_id'] != cluster or item['name'] not in NAMES:
            raise ValueError('Unexpected node group; refuse unscoped cleanup')
        if item['name'] in result:
            raise ValueError('Duplicate group name')
        result[item['name']] = item['id']
    return result


def metadata(path):
    req = urllib.request.Request('http://metadata.nebius.internal/v1/' + path,
                                 headers={'Metadata': 'true'})
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.read().decode()


async def main():
    from nebius.sdk import SDK
    from nebius.api.nebius.mk8s.v1 import NodeGroupServiceClient, ListNodeGroupsRequest, DeleteNodeGroupRequest
    p = argparse.ArgumentParser()
    p.add_argument('--cluster', required=True)
    p.add_argument('--project', required=True)
    p.add_argument('--deadline', type=float, required=True)
    a = p.parse_args()
    info = json.loads(metadata('instance-data'))
    if info['parent_id'] != a.project or not info.get('service_account_id'):
        raise ValueError('Guard has wrong project or lacks its own identity')
    ready = False
    deadline=a.deadline
    while True:
        try:
            async with SDK(credentials=metadata('iam/sa/token/access_token').strip()) as sdk:
                service = NodeGroupServiceClient(sdk)
                response = await service.list(ListNodeGroupsRequest(parent_id=a.cluster), timeout=20)
                if response.next_page_token:
                    raise ValueError('Unexpected pagination')
                groups = select_groups([{'id': x.metadata.id, 'name': x.metadata.name,
                                          'parent_id': x.metadata.parent_id} for x in response.items], a.cluster)
                update=Path('/results/deadline-request.json')
                if update.exists():
                    try:
                        changed=shortened_deadline(deadline,json.loads(update.read_text()),a.cluster)
                        if changed!=deadline:deadline=changed;ready=False
                    except (ValueError,TypeError):
                        print('Rejected deadline update; original cleanup remains armed.',flush=True)
                if not ready:
                    if 'router-cpu' not in groups:
                        raise ValueError('CPU guard group missing')
                    record = {'armed': True, 'cluster': a.cluster, 'project': a.project,
                              'deadline': deadline, 'service_account': info['service_account_id'],
                              'gpu_groups_present_at_arm': sorted(set(groups)-{'router-cpu'})}
                    ready_path=Path('/results/guard-ready.json');temp=ready_path.with_suffix('.tmp')
                    temp.write_text(json.dumps(record));temp.replace(ready_path)
                    print(json.dumps(record), flush=True)
                    ready = True
                if time.time() >= deadline:
                    gpu = [groups[x] for x in ('router-local','router-remote') if x in groups]
                    for identifier in gpu:
                        try:
                            operation = await service.delete(DeleteNodeGroupRequest(id=identifier), timeout=25)
                            print(json.dumps({'delete_requested': identifier, 'operation': operation.id}), flush=True)
                        except Exception as error:
                            print('Delete pending/retry: '+type(error).__name__, flush=True)
                    if not gpu and 'router-cpu' in groups:
                        await service.delete(DeleteNodeGroupRequest(id=groups['router-cpu']), timeout=25)
                        print('Both GPU groups absent; CPU self-deletion requested.', flush=True)
        except Exception as error:
            # No credential-bearing request/exception repr in logs.
            print('Guard retry: '+type(error).__name__, flush=True)
        await asyncio.sleep(5 if time.time() >= deadline else 15)


if __name__ == '__main__':
    asyncio.run(main())
