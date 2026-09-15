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

resource "nebius_mk8s_v1_cluster" "topology" {
  parent_id = var.project_id
  name      = "router-topology"
  control_plane = {
    subnet_id = var.subnet_id
    version   = "1.35"
    endpoints = { public_endpoint = {} }
  }
}

resource "nebius_mk8s_v1_node_group" "cpu" {
  parent_id        = nebius_mk8s_v1_cluster.topology.id
  name             = "router-cpu"
  fixed_node_count = 1
  template = {
    resources          = { platform = "cpu-d3", preset = "16vcpu-64gb" }
    boot_disk          = { type = "NETWORK_SSD", size_gibibytes = 64 }
    network_interfaces = [{ subnet_id = var.subnet_id }]
  }
}

resource "nebius_mk8s_v1_node_group" "gpu" {
  for_each = {
    local  = "2gpu-64vcpu-384gb"
    remote = "1gpu-16vcpu-96gb"
  }
  parent_id        = nebius_mk8s_v1_cluster.topology.id
  name             = "router-${each.key}"
  fixed_node_count = 1
  version          = "1.35"
  template = {
    resources          = { platform = "gpu-l40s-d", preset = each.value }
    gpu_settings       = { drivers_preset = "cuda13.0" }
    boot_disk          = { type = "NETWORK_SSD", size_gibibytes = 256 }
    network_interfaces = [{ subnet_id = var.subnet_id }]
    preemptible        = {}
  }
}

output "cluster_id" {
  value = nebius_mk8s_v1_cluster.topology.id
}

output "node_group_ids" {
  value = merge({ cpu = nebius_mk8s_v1_node_group.cpu.id }, {
    for role, group in nebius_mk8s_v1_node_group.gpu : role => group.id
  })
}
