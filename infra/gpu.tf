# GPU node group — the Terraform delta.
#
# TOY SETUP DISCLOSURE. This file is the rehearsal shape: a two-machine session
# on single-GPU L40S nodes over plain TCP, no InfiniBand, with a small model. It
# is published because the shape and the reasoning transfer, not because
# anything was measured on it. On the 8xH200 cluster the platform and preset are
# re-pointed, the model changes, the engines request RDMA devices, and the
# InfiniBand GPU-cluster block below comes back. No number from the rehearsal is
# imported into this repository.
#
# WHAT THE H200 VERSION CHANGES, precisely:
#   1. `platform` and `preset` re-pointed to the 8xH200 machine type;
#   2. the InfiniBand GPU-cluster resource added back
#      (`nebius_compute_v1_gpu_cluster`) and this node group joined to it through
#      a `gpu_cluster` block in the template — that is what puts both nodes on
#      one fabric;
#   3. `fixed_node_count` stays 2, but each node now carries eight cards, so the
#      "one GPU per node" placement guarantee described at the bottom of this
#      file disappears and only the pod anti-affinity survives.
#
# THIS FILE IS A DELTA, not a standalone configuration. It sits beside a base
# `main.tf` (not published here) that declares three things: the managed
# Kubernetes cluster, a CPU node group for the router and the benchmark harness,
# and this GPU node group.
#
# TWO nodes at ONE GPU each, NOT one node with two GPUs. The point of the shape
# is that the key/value cache has to cross a real network.

resource "nebius_mk8s_v1_node_group" "gpu" {
  # Node groups parent to the CLUSTER, not the project.
  parent_id        = nebius_mk8s_v1_cluster.this.id
  name             = "gpu"
  fixed_node_count = 2
  version          = var.k8s_version

  template = {
    # The Intel-host L40S platform offers only 1-GPU presets, which is what the
    # rehearsal wants: two separate single-GPU machines. Re-pointed to the
    # 8xH200 platform and preset for the measured runs.
    resources = {
      platform = "gpu-l40s-a"
      preset   = "1gpu-8vcpu-32gb"
    }

    # The driver preset is empirical, not documented: the provider reference
    # lists no driver preset for this Kubernetes version on any platform. There
    # is no separate driver-install action — the preset makes the drivers and
    # the device plugin arrive preinstalled.
    gpu_settings = { drivers_preset = var.drivers_preset }

    boot_disk          = { type = "NETWORK_SSD", size_gibibytes = 100 }
    network_interfaces = [{ subnet_id = var.subnet_id }]

    # Preemptible (spot): a discounted instance the provider may reclaim at any
    # time. `{}` is this provider's convention for "enabled". `preemptible` and
    # `reservation_policy` are MUTUALLY EXCLUSIVE — set one, null the other.
    # The rerun rule for a mid-run reclaim is in results/README.md.
    preemptible        = var.gpu_preemptible ? {} : null
    reservation_policy = var.gpu_preemptible ? null : { policy = "FORBID" }
  }

  # NO gpu_cluster BLOCK, deliberately. There is no
  #   gpu_cluster = { id = nebius_compute_v1_gpu_cluster.X.id }
  # here and no GPU-cluster resource beside it, because the L40S is a PCIe card
  # the provider documents as incompatible with its InfiniBand grouping. There
  # is no fabric to join, so the cache rides plain TCP over the ordinary virtual
  # network. That is exactly why no rehearsal number can say anything about a
  # fabric, and exactly what the H200 configuration adds back.
}

# HOW PREFILL AND DECODE ARE GUARANTEED TO LAND ON DIFFERENT MACHINES here:
# each pod requests one GPU, each node has exactly one allocatable GPU, so the
# scheduler has no room to place the second pod beside the first. That is a
# guarantee from resource accounting, not a hope about spreading. The engine
# patches carry a second, independent guarantee — a required pod anti-affinity
# on the role label with topologyKey kubernetes.io/hostname — and on eight-GPU
# nodes that second one is the only one left.
