#!/usr/bin/env python3
"""Frozen token-ID data adapter for pinned inference-perf; use its scheduler/client/reports."""
import json,uuid,time,re,os
from pathlib import Path
from inference_perf.apis import InferenceAPIData,InferenceInfo,StreamedResponseMetrics
from inference_perf.apis.streaming_parser import parse_sse_stream
from inference_perf.payloads import RequestMetrics,Text
from inference_perf.config import APIType
from inference_perf.datagen.base import DataGenerator

class TokenIDsOnly:
    """No text tokenization: inputs are token IDs and outputs use validated server usage."""
    def __init__(self,*args,**kwargs):pass
    def count_tokens(self,*args,**kwargs):raise RuntimeError('Unexpected tokenizer use in frozen-ID adapter')

class FrozenRequest(InferenceAPIData):
    body:dict
    request_key:str
    kind:str
    counts:dict={}
    observation_error:str=''
    def get_api_type(self):return APIType.Completion
    def get_route(self):return '/v1/completions'
    async def to_request_body(self,*_,**kwargs):
        if os.environ.get('ROUTER_METRICS_URL'):
            import aiohttp
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
                    async with session.get(os.environ['ROUTER_METRICS_URL']) as response:
                        response.raise_for_status();raw=await response.text()
                self.counts={'local':0,'remote':0,'sample_unix':time.time()}
                for line in raw.splitlines():
                    if '_inflight_requests{' not in line:continue
                    for role in ('local','remote'):
                        if f'endpoint_name="{role}-rank-0"' in line:self.counts[role]+=float(line.rsplit(' ',1)[1])
            except Exception as error:self.observation_error=type(error).__name__
        return self.body
    async def process_response(self,response,config,tokenizer,lora_adapter=None):
        text,times,raw,chunks,usage=await parse_sse_stream(response,extract_content=lambda d: next((x.get('text') for x in d.get('choices',[]) if x.get('text')),None))
        if '[DONE]' not in raw or not times or not usage:raise ValueError('Incomplete stream or usage')
        if usage.get('prompt_tokens')!=len(self.body['prompt']) or usage.get('completion_tokens')!=self.body['max_tokens']:
            raise ValueError('Input/output token count differs from frozen workload')
        events=[json.loads(line[5:].strip()) for line in raw.splitlines() if line.startswith('data:') and line[5:].strip()!='[DONE]']
        if any(x.get('error') for x in events):raise ValueError('Error in generated stream')
        if [c['finish_reason'] for x in events for c in x.get('choices',[]) if c.get('finish_reason')]!=['length']:
            raise ValueError('Unexpected finish reason')
        return InferenceInfo(request_metrics=RequestMetrics(text=Text(input_tokens=len(self.body['prompt']))),
            response_metrics=StreamedResponseMetrics(response_chunks=chunks,chunk_times=times,output_tokens=usage['completion_tokens'],output_token_times=times,server_usage=usage),
            extra_info={'raw_response':raw,'request_key':self.request_key,'kind':self.kind,
                        'pre_request_counts':self.counts,'observation_error':self.observation_error},labels={'kind':self.kind})

class FrozenGenerator(DataGenerator):
    def __init__(self,api_config,config,tokenizer):
        super().__init__(api_config,config,tokenizer)
        self.doc=json.loads(Path(config.path).read_text())
    def get_supported_apis(self):return [APIType.Completion]
    def is_io_distribution_supported(self):return False
    def is_shared_prefix_supported(self):return False
    def get_request_count(self):return len(self.doc['requests'])
    def get_data(self):
        for row in self.doc['requests']:
            key=str(uuid.UUID(bytes=uuid.uuid5(uuid.NAMESPACE_URL,self.doc['trial']['id']+'/'+str(row['index'])).bytes,version=4))
            headers={'x-request-id':key,'x-benchmark-run':self.doc['trial']['id']}
            if row['pin']:
                if self.doc['trial']['mode']!='calibration':raise ValueError('Evaluation traffic must be unpinned')
                headers['x-benchmark-decoder']=self.doc['route_tokens'][row['pin']]
            yield FrozenRequest(body=row['body'],request_key=key,kind=row['kind'],headers=headers)

if __name__=='__main__':
    import inference_perf.main as main
    import inference_perf.client.modelserver.openai_client as client
    main.MockDataGenerator=FrozenGenerator
    # Native HTTP/SSE/load timing are unchanged. Skip irrelevant HF tokenizer download.
    main.CustomTokenizer=TokenIDsOnly;client.CustomTokenizer=TokenIDsOnly
    main.main_cli()
