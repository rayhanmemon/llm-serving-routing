#!/usr/bin/env python3
"""Parse every engine command with installed vLLM, without loading a model."""
import argparse
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent


def validate(config_path):
    import vllm
    from vllm.utils.argparse_utils import FlexibleArgumentParser
    from vllm.entrypoints.cli.serve import ServeSubcommand
    sp=importlib.util.spec_from_file_location('settings',HERE/'tp4-config.py')
    settings=importlib.util.module_from_spec(sp);sp.loader.exec_module(settings)
    config,_=settings.load_config(config_path)
    if not vllm.__version__.startswith(config.get('vllm_version','0.26.0')):raise ValueError('Wrong vLLM parser version')
    for host in ['local','remote']:
        for command in settings.engine_specs(config,host,'127.0.0.1'):
            parser=FlexibleArgumentParser()
            ServeSubcommand().subparser_init(parser.add_subparsers(dest='subparser'))
            args=parser.parse_args(command['command'][1:])
            args.model=args.model_tag
            ServeSubcommand().validate(args)
            if args.model!=config['model'] or args.revision!=config['revision']:
                raise ValueError('Parsed model/revision differs from pinned settings')
            if args.tensor_parallel_size!=4 or args.max_model_len!=131072:
                raise ValueError('Parsed TP/context differs from selected setup')
            kv=args.kv_transfer_config
            extra=kv['kv_connector_extra_config'] if isinstance(kv,dict) else kv.kv_connector_extra_config
            if extra.get('enable_cross_layers_blocks',False) is not config.get('enable_cross_layers_blocks',False):
                raise ValueError('Packed-layout option did not survive native parsing')
            if args.hf_overrides!={'rope_scaling':config['rope_scaling']}:
                raise ValueError('YaRN override did not survive native parsing')
    return {'native_vllm_parser_passed':True,'version':vllm.__version__,'engine_commands':3,'model_loaded':False}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args()
    print(json.dumps(validate(a.config)))
