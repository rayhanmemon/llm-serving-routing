"""Account every completed routed request against real KV transfer counters."""
import importlib.util,json,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('c',HERE/'tp4-client.py');c=importlib.util.module_from_spec(s);s.loader.exec_module(c)

def verify(before,after,rows,routes,ips,architecture):
    expected={role:{'requests':0,'bytes':0} for role in ips}
    for row in rows:
        upstream=routes[row['request_key']]['upstream_host']
        roles=[role for role,ip in ips.items() if upstream.startswith(ip+':')]
        if len(roles)!=1:raise ValueError('Ambiguous or missing decoder route')
        target=expected[roles[0]];target['requests']+=1
        target['bytes']+=c.settings.cache_bytes_per_rank(architecture,row['input_tokens'])*4
    for role in ips:
        a=before[role]['values'];b=after[role]['values'];e=expected[role]
        for metric in (c.e.COUNT,c.e.TIME_COUNT):
            if b[metric]-a[metric]!=4*e['requests']:raise ValueError('Missing/extra KV transfers: '+role)
        if b[c.e.BYTES]-a[c.e.BYTES]!=e['bytes']:raise ValueError('Wrong total KV payload: '+role)
    for role in before:
        if any(after[role]['values'][m]!=before[role]['values'][m] for m in c.e.FAILURES):raise ValueError('KV transfer failure counter changed')
    return {'validated':True,'expected':expected}


def warmup(hosts,url,body):
    import urllib.request
    before=c.snapshot(hosts)
    request=urllib.request.Request(url+'/v1/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','x-benchmark-run':'declared-warmup'})
    with urllib.request.urlopen(request,timeout=15) as response:
        if b'[DONE]' not in response.read():raise ValueError('Incomplete warmup')
    for _ in range(40):
        after=c.snapshot(hosts)
        count=sum(after[r]['values'][c.e.COUNT]-before[r]['values'][c.e.COUNT] for r in hosts)
        if any(after[r]['values'][m]!=before[r]['values'][m] for r in before for m in c.e.FAILURES):raise ValueError('Transfer error during warmup')
        if count==4:return
        if count>4:raise ValueError('Unexpected extra transfer during warmup')
        time.sleep(.25)
    raise ValueError('Warmup transfer counters did not settle')
