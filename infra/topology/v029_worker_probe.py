"""Read-only startup evidence from the actual vLLM GPU worker allocation."""
import json
import os
from pathlib import Path

from vllm.v1.worker.gpu_worker import Worker


class ProbedWorker(Worker):
    def initialize_from_config(self, kv_cache_config):
        result = super().initialize_from_config(kv_cache_config)
        runner = self.model_runner
        tensors = runner.kv_caches
        if len(tensors) != 64:
            raise ValueError('Expected 64 Qwen3 cache layer views')
        record = {
            'role': os.environ['TP4_ROLE'],
            'rank': self.rank,
            'runner_class': type(runner).__module__ + '.' + type(runner).__name__,
            'use_v2': self.vllm_config.use_v2_model_runner,
            'layout': self.vllm_config.cache_config.get_resolved_kv_cache_layout().name,
            'num_blocks': runner.kv_cache_config.num_blocks,
            'layer_count': len(tensors),
            'kernel_block_sizes': list(runner.kernel_block_sizes),
            'attention_backends': sorted({g.backend.get_name() for groups in runner.attn_groups for g in groups}),
            'shape': list(tensors[0].shape),
            'stride': list(tensors[0].stride()),
            'element_bytes': tensors[0].element_size(),
            'storage_bytes': tensors[0].untyped_storage().nbytes(),
            'shared_storage_count': len({t.untyped_storage().data_ptr() for t in tensors}),
        }
        if not record['use_v2'] or record['layout'] != os.environ['VLLM_KV_CACHE_LAYOUT']:
            raise ValueError('Configured runner/layout did not activate')
        # Data pointers are only used for an equality count; no memory content is read.
        out = Path(os.environ['TP4_RESULTS_DIR'])
        target = out / f"layout-probe-{record['role']}-{record['rank']}.json"
        target.write_text(json.dumps(record, indent=2) + '\n')
        return result
