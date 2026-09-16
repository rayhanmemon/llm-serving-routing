#!/usr/bin/env python3
"""Render a CPU utility Pod and ConfigMap containing correctness-client.py."""
import argparse
import json
from pathlib import Path


IMAGE = "quay.io/inference-perf/inference-perf:v0.6.1@sha256:e29328cc223ebae58d9022d60ad651cc3c4cbd534885a78b28f54086aa4b9c9e"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-node", required=True, help="Actual Kubernetes CPU node name")
    parser.add_argument("--name", default="topology-correctness")
    parser.add_argument("--namespace", default="topology-measurement")
    args = parser.parse_args()

    client = Path(__file__).with_name("correctness-client.py").read_text()
    config = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": args.name, "namespace": args.namespace},
        "data": {"correctness-client.py": client},
    }
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": args.name,
            "namespace": args.namespace,
            "labels": {"app.kubernetes.io/name": "topology-correctness"},
        },
        "spec": {
            "nodeSelector": {"kubernetes.io/hostname": args.cpu_node},
            "restartPolicy": "Never",
            "activeDeadlineSeconds": 1800,
            "containers": [{
                "name": "client",
                "image": IMAGE,
                "command": ["sh", "-c", "sleep 1500"],
                "resources": {
                    "requests": {"cpu": "100m", "memory": "128Mi"},
                    "limits": {"memory": "512Mi"},
                },
                "volumeMounts": [
                    {"name": "client", "mountPath": "/opt/probe", "readOnly": True},
                    {"name": "results", "mountPath": "/results"},
                ],
            }],
            "volumes": [
                {"name": "client", "configMap": {"name": args.name}},
                {"name": "results", "emptyDir": {}},
            ],
        },
    }
    print(json.dumps({"apiVersion": "v1", "kind": "List", "items": [config, pod]}, indent=2))


if __name__ == "__main__":
    main()
