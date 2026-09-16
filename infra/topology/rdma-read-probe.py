#!/usr/bin/env python3
"""Byte-check NIXL READ between isolated GPU Pods. No model inference."""
import argparse
import base64
import json
import os
from pathlib import Path
import resource
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.request


def main():
    p = argparse.ArgumentParser()
    p.add_argument('role', choices=['producer', 'consumer'])
    p.add_argument('--peer')
    p.add_argument('--out', default='/results')
    p.add_argument('--lifetime', type=int, default=1200)
    a = p.parse_args()
    import torch
    from nixl._api import nixl_agent, nixl_agent_config
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    torch.cuda.set_device(0)
    size = 1024 ** 3
    buf = torch.empty(size, dtype=torch.uint8, device='cuda')
    buf.fill_(73 if a.role == 'producer' else 0)
    torch.cuda.synchronize()
    agent = nixl_agent('rdma-' + a.role + '-' + os.environ['HOSTNAME'], nixl_agent_config(backends=['UCX']))
    reg = agent.register_memory(buf, backends=['UCX'])
    metadata = {'metadata': base64.b64encode(agent.get_agent_metadata()).decode(),
                'address': buf.data_ptr(), 'bytes': size, 'device': 0}
    info = {'role': a.role, 'memlock': resource.getrlimit(resource.RLIMIT_MEMLOCK),
            'gpu_name': torch.cuda.get_device_name(0), 'visible_gpu_count': torch.cuda.device_count(),
            'ucx_tls': os.environ.get('UCX_TLS'), 'started_unix': time.time()}
    (out / 'environment.json').write_text(json.dumps(info, indent=2))
    (out / 'ready').write_text('registered')
    if a.role == 'producer':
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(metadata).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(body)
        server = HTTPServer(('0.0.0.0', 8123), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        time.sleep(a.lifetime)
        server.shutdown()
    else:
        with urllib.request.urlopen(a.peer + '/metadata', timeout=15) as response:
            remote_meta = json.load(response)
        remote = agent.add_remote_agent(base64.b64decode(remote_meta['metadata']))
        rows = []
        for amount in (262144, 64 * 1024 ** 2, 1024 ** 3):
            for repeat in range(3):
                view = buf[:amount]; view.zero_(); torch.cuda.synchronize()
                local_desc = agent.get_xfer_descs(view)
                remote_desc = agent.get_xfer_descs([(remote_meta['address'], amount, 0)], 'VRAM')
                handle = agent.initialize_xfer('READ', local_desc, remote_desc, remote, backends=['UCX'])
                start = time.monotonic(); state = agent.transfer(handle)
                while state == 'PROC' and time.monotonic() - start < 60:
                    time.sleep(.001); state = agent.check_xfer_state(handle)
                if state != 'DONE': raise RuntimeError('NIXL transfer state ' + state)
                torch.cuda.synchronize(); elapsed = time.monotonic() - start
                valid = bool(torch.all(view == 73).item())
                row = {'bytes': amount, 'repeat': repeat, 'elapsed_seconds': elapsed,
                       'all_bytes_equal_73': valid, 'checksum': int(view.sum().item())}
                rows.append(row)
                (out / 'transfers.json').write_text(json.dumps(rows, indent=2))
                print('READ_RESULT', json.dumps(row), flush=True)
                agent.release_xfer_handle(handle)
                if not valid: raise RuntimeError('GPU payload mismatch')
        (out / 'PASS').write_text('All nine READs verified byte-for-byte; transport logs must establish RDMA.')
        agent.deregister_memory(reg)

if __name__ == '__main__':
    main()
