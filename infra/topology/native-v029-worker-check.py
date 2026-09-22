import json,os,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import torch
from vllm.config import CacheConfig
from vllm.v1.kv_cache_layout import KVCacheLayout
from vllm.v1.kv_cache_interface import FullAttentionSpec,KVCacheGroupSpec
from vllm.v1.core.kv_cache_utils import get_kv_cache_config_from_groups
from vllm.v1.worker.utils import allocate_kv_cache
from v029_worker_probe import ProbedWorker,Worker
with tempfile.TemporaryDirectory() as d:
 for name in ('LBHNC','BLHNC','BHLNC'):
  cc=CacheConfig(block_size=64,enable_prefix_caching=False);cc.kv_cache_layout=name
  cfg=SimpleNamespace(cache_config=cc,use_v2_model_runner=True)
  spec=FullAttentionSpec(block_size=64,num_kv_heads=2,head_size=128,dtype=torch.bfloat16)
  group=KVCacheGroupSpec(layer_names=[f'layer{i}' for i in range(64)],kv_cache_spec=spec)
  plan=get_kv_cache_config_from_groups(cfg,[group],spec.page_size_bytes*64*8)
  views=allocate_kv_cache(plan,torch.device('cpu'),KVCacheLayout[name],[64])
  worker=object.__new__(ProbedWorker);worker.rank=0;worker.vllm_config=cfg
  worker.model_runner=SimpleNamespace(kv_caches=list(views.values()),kv_cache_config=plan,kernel_block_sizes=[64],attn_groups=[[SimpleNamespace(backend=SimpleNamespace(get_name=lambda:"FLASH_ATTN"))]])
  os.environ.update(TP4_ROLE='prefill',TP4_RESULTS_DIR=d,VLLM_KV_CACHE_LAYOUT=name)
  with patch.object(Worker,'initialize_from_config',return_value=None) as base:
   worker.initialize_from_config(plan);base.assert_called_once_with(plan)
  record=json.loads((Path(d)/'layout-probe-prefill-0.json').read_text())
  assert record['layout']==name and record['use_v2'] and record['shared_storage_count']==1
 print(json.dumps({'native_worker_probe_extraction_passed':True,'gpu_initialization':'Substituted in CPU test; live GPU proof still required'}))
