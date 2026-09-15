#!/usr/bin/env python3
"""Inspect allocated GPU identities/capabilities; this does not measure KV transfer."""
import argparse
import ctypes
import json
import subprocess
import uuid

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--expected-count', type=int, default=1)
a = p.parse_args()
cuda = ctypes.CDLL('libcuda.so.1')
def check(code):
    if code:
        raise RuntimeError('CUDA driver error ' + str(code))
check(cuda.cuInit(0))
count = ctypes.c_int()
check(cuda.cuDeviceGetCount(ctypes.byref(count)))
if count.value != a.expected_count:
    raise SystemExit(f'Expected {a.expected_count} allocated GPU(s), driver sees {count.value}')
devices = []
handles = []
for i in range(count.value):
    handle = ctypes.c_int()
    check(cuda.cuDeviceGet(ctypes.byref(handle), i))
    handles.append(handle.value)
    raw = (ctypes.c_ubyte * 16)()
    check(cuda.cuDeviceGetUuid(ctypes.byref(raw), handle.value))
    devices.append('GPU-' + str(uuid.UUID(bytes=bytes(raw))))
peers = []
for i in range(count.value):
    for j in range(count.value):
        if i != j:
            accessible = ctypes.c_int()
            check(cuda.cuDeviceCanAccessPeer(ctypes.byref(accessible), handles[i], handles[j]))
            peers.append({'from': i, 'to': j, 'can_access_peer': bool(accessible.value)})
info = subprocess.check_output(['nvidia-smi', '--query-gpu=uuid,name,pci.bus_id,memory.total,driver_version,clocks.current.sm,clocks.current.memory,utilization.gpu', '--format=csv'], text=True)
topology = subprocess.check_output(['nvidia-smi', 'topo', '-m'], text=True)
print(json.dumps({'gpu_uuids': devices, 'peer_capabilities': peers, 'nvidia_smi': info, 'topology': topology}))
