# LEGACY 1P/1D SCAFFOLDING: this does not instantiate the selected topology
# evaluation. It needs both a local and a remote decoder on distinct physical
# GPUs, truthful placement labels and measured transfer asymmetry. Revise and
# validate before use; no topology evaluation has been executed.
#
# GPU node group — the Terraform delta.
#
# STARTING CONFIGURATION. Two single-GPU L40S nodes over TCP, with a small
# model. Validate this configuration for the router evaluation and derive
# thresholds on that deployment. No measured result is supplied by this file.
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
    # The Intel-host L40S platform uses single-GPU presets here, giving
    # separate machines for prompt processing and generation.
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
  # network. Report this transport explicitly in any evaluation.
}

# HOW PREFILL AND DECODE ARE GUARANTEED TO LAND ON DIFFERENT MACHINES here:
# each pod requests one GPU, each node has exactly one allocatable GPU, so the
# scheduler has no room to place the second pod beside the first. That is a
# guarantee from resource accounting, not a hope about spreading. The engine
# patches carry a second, independent guarantee — a required pod anti-affinity
# on the role label with topologyKey kubernetes.io/hostname.
