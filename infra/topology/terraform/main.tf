terraform {
  required_providers {
    nebius = {
      source  = "nebius/nebius"
      version = "0.6.46"
    }
  }
}

provider "nebius" {
  profile = { name = var.nebius_profile }
}

resource "nebius_compute_v1_gpu_cluster" "local" {
  parent_id         = var.project_id
  name              = "router-local-h100"
  infiniband_fabric = var.infiniband_fabric
}

resource "nebius_mk8s_v1_cluster" "topology" {
  parent_id = var.project_id
  name      = "router-topology"
  control_plane = {
    subnet_id = var.subnet_id
    version   = "1.35"
    endpoints = { public_endpoint = {} }
  }
}

resource "nebius_mk8s_v1_node_group" "local" {
  parent_id        = nebius_mk8s_v1_cluster.topology.id
  name             = "router-local"
  fixed_node_count = 1
  version          = "1.35"
  template = {
    resources          = { platform = "gpu-h100-sxm", preset = "8gpu-128vcpu-1600gb" }
    gpu_cluster        = { id = nebius_compute_v1_gpu_cluster.local.id }
    gpu_settings       = { drivers_preset = "cuda13.0" }
    boot_disk          = { type = "NETWORK_SSD", size_gibibytes = 256 }
    network_interfaces = [{ subnet_id = var.subnet_id }]
    preemptible        = var.gpu_preemptible ? {} : null
    reservation_policy = var.gpu_preemptible ? null : { policy = "FORBID" }
  }
}

resource "nebius_mk8s_v1_node_group" "cpu" {
  count            = var.ipc_diagnostic_only ? 0 : 1
  parent_id        = nebius_mk8s_v1_cluster.topology.id
  name             = "router-cpu"
  fixed_node_count = 1
  template = {
    resources          = { platform = "cpu-d3", preset = "16vcpu-64gb" }
    boot_disk          = { type = "NETWORK_SSD", size_gibibytes = 64 }
    network_interfaces = [{ subnet_id = var.subnet_id }]
  }

  depends_on = [nebius_mk8s_v1_node_group.local]
}

resource "nebius_mk8s_v1_node_group" "remote" {
  count            = var.ipc_diagnostic_only ? 0 : 1
  parent_id        = nebius_mk8s_v1_cluster.topology.id
  name             = "router-remote"
  fixed_node_count = 1
  version          = "1.35"
  template = {
    resources          = { platform = "gpu-h100-sxm", preset = "1gpu-16vcpu-200gb" }
    gpu_cluster        = null
    gpu_settings       = { drivers_preset = "cuda13.0" }
    boot_disk          = { type = "NETWORK_SSD", size_gibibytes = 256 }
    network_interfaces = [{ subnet_id = var.subnet_id }]
    preemptible        = var.gpu_preemptible ? {} : null
    reservation_policy = var.gpu_preemptible ? null : { policy = "FORBID" }
  }

  depends_on = [nebius_mk8s_v1_node_group.local]
}

output "cluster_id" {
  value = nebius_mk8s_v1_cluster.topology.id
}

output "node_group_ids" {
  value = {
    cpu    = try(nebius_mk8s_v1_node_group.cpu[0].id, null)
    local  = nebius_mk8s_v1_node_group.local.id
    remote = try(nebius_mk8s_v1_node_group.remote[0].id, null)
  }
}
