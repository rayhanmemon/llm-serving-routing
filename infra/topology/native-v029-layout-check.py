import ast,hashlib,inspect,json,os
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import torch,vllm
from vllm.config import CacheConfig
from vllm.v1.kv_cache_layout import KVCacheLayout
from vllm.v1.kv_cache_interface import FullAttentionSpec,KVCacheGroupSpec
from vllm.v1.core.kv_cache_utils import get_kv_cache_config_from_groups
from vllm.v1.worker.utils import allocate_kv_cache
from vllm.v1.attention.backends.utils import resolve_kv_cache_layout
root=Path(vllm.__file__).parent
source=(root/'distributed/kv_transfer/kv_connector/v1/nixl/base_worker.py').read_text()
tree=ast.parse(source)
klass=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='NixlBaseConnectorWorker')
methods=[n for n in klass.body if isinstance(n,ast.FunctionDef) and n.name in ('_stack_descs','_build_fa_local','_compute_desc_ids')]
module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),ast.ClassDef(name='DescriptorMethods',bases=[],keywords=[],body=methods,decorator_list=[])],type_ignores=[])
ast.fix_missing_locations(module);ns={'np':np};exec(compile(module,'actual-vllm-descriptor-methods','exec'),ns)
results=[]
for name in ('LBHNC','BLHNC','BHLNC'):
 cc=CacheConfig(block_size=64,enable_prefix_caching=False)
 os.environ['VLLM_KV_CACHE_LAYOUT']=name
 cfg=SimpleNamespace(cache_config=cc)
 spec=FullAttentionSpec(block_size=64,num_kv_heads=2,head_size=128,dtype=torch.bfloat16)
 group=KVCacheGroupSpec(layer_names=[f'layer{i}' for i in range(64)],kv_cache_spec=spec)
 resolved=resolve_kv_cache_layout(cfg,[[x.name for x in KVCacheLayout]]*4,[spec]);assert resolved.name==name
 plan=get_kv_cache_config_from_groups(cfg,[group],available_memory=spec.page_size_bytes*64*8)
 layout=KVCacheLayout[name];views=allocate_kv_cache(plan,torch.device('cpu'),layout,[64])
 assert plan.num_blocks==8 and len(views)==64
 tensors=list(views.values());storage=tensors[0].untyped_storage();assert len({x.untyped_storage().data_ptr() for x in tensors})==1
 # Reproduce region selection from the installed registration code; native CPU views supply actual addresses/strides.
 regions=[]
 for cache in tensors:
  stride=cache.stride(0)*cache.element_size();block_major=8*stride==storage.nbytes()
  hnc=cache.stride(2)==cache.shape[3] and cache.stride(1)==cache.shape[2]*cache.shape[3]
  addr,length,step=(storage.data_ptr(),storage.nbytes()//8,storage.nbytes()//8) if block_major and not hnc else (cache.data_ptr(),spec.page_size_bytes,stride)
  if addr not in [x[0] for x in regions]:regions.append((addr,length,step))
 obj=ns['DescriptorMethods']();obj.num_blocks=8;obj.device_id=0;obj.transfer_topo=True;obj.num_regions=len(regions);obj._has_mamba=False
 obj.block_len_per_layer=[x[1] for x in regions];obj.block_stride_per_layer=[x[2] for x in regions]
 descs=obj._build_fa_local([x[0] for x in regions],1)
 ids=obj._compute_desc_ids([[3,2,1,0]],8,None,1);selected=descs[ids]
 assert sum(int(x[1]) for x in selected)==4*64*spec.page_size_bytes
 results.append({'layout':name,'bytes':storage.nbytes(),'layer_shape':list(tensors[0].shape),'layer_stride':list(tensors[0].stride()),'planned_layer_stride_bytes':plan.kv_cache_tensors[0].layer_stride,'planned_block_stride_bytes':plan.kv_cache_tensors[0].block_stride,'inferred_registered_regions':len(regions),'selected_descs_before_nixl_merging':len(selected),'first_addresses_relative':[int(x[0])-storage.data_ptr() for x in selected[:8]]})
print(json.dumps({'vllm_version':vllm.__version__,'native_cpu_allocation':True,'native_environment_resolution':True,'gpu_execution':False,'registration_region_selection':'Source-derived model, not live NIXL registration','layouts':results},indent=2))
