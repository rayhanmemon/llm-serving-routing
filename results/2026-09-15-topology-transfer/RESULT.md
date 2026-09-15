# Placement failed; no inference measurement

The two-GPU local VM failed to place in Nebius eu-north1. Its create operation finished at 15:33:42 UTC with `NotEnoughResources` for `2gpu-64vcpu-384gb` (VM scheduling timeout). The CPU node and single-GPU remote node did start. The local VM became STOPPED and never joined Kubernetes.

Provisioning began **2026-09-15 15:23:49 UTC**. After preserving the failure evidence, Terraform apply was interrupted and all four managed resources were destroyed. Independent API listings verified zero instances, clusters, disks and filesystems at **15:47:46 UTC**. Session wall time: **24 minutes**. No registry or object-storage resources were created; the deadline guard exited after cleanup.

**Estimated cost: approximately $0.60 before tax**, not invoice-verified. A conservative calculation counts create-operation start through delete-operation finish for the CPU ($0.1096) and remote GPU VM ($0.4638), plus an allowance for 576 GiB of disks across the full session (about $0.0224). This includes some non-billed startup/deletion time. The two-GPU VM never ran and is assigned zero compute cost. Exact billed durations may be smaller.

No inference request was sent. No model engine started, no KV transfer was measured, and the prediction in EXPECTED.md remains untested. This attempt supplies no routing-performance evidence.

Verified during preparation/runtime:

- The reviewed AMD64 EPP image was imported directly onto the CPU node and the helper pod was deleted. Nebius places its containerd client at `/usr/local/bin/ctr`; import now discovers the existing path and checks the existing socket without changing runtime configuration.
- The deployed EPP/Envoy pod reached 2/2 ready. The custom Envoy access-log configuration passed validation in the proxy image.
- The engine manifest was initially rejected because a YAML `y` string became a boolean. The serializer now emits quoted `yes`; all three engine deployments passed Kubernetes server-side dry-run after this correction. They were not launched afterward because placement had failed.
- The CPU benchmark image lacks `nvidia-smi`; the attempted GPU metadata probe stopped at that missing utility. Use the GPU engine image for that inspection on the next attempt. No GPU peer-access result was obtained.

The next attempt needs available multi-GPU capacity and a new bounded session decision. Keep the intended topology and fair comparison; do not treat the working single-GPU node as a substitute for a local/remote experiment.

See provisioning/ for the provider failure, Kubernetes observations, EPP logs, image-import logs, and teardown evidence. The original collection.json covers the initial snapshot; checksums.json covers the final run record.
