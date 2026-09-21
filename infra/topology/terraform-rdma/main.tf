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

locals {
  gpu_shapes = {
    H100 = {
      platform      = "gpu-h100-sxm"
      local_preset  = "8gpu-128vcpu-1600gb"
      remote_preset = "1gpu-16vcpu-200gb"
    }
    H200 = {
      platform      = "gpu-h200-sxm"
      local_preset  = "8gpu-128vcpu-1600gb"
      remote_preset = "1gpu-16vcpu-200gb"
    }
  }
  gpu_shape = local.gpu_shapes[var.gpu_platform]
}

resource "nebius_compute_v1_gpu_cluster" "local" {
  parent_id         = var.project_id
  name              = "router-local-${lower(var.gpu_platform)}"
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
  strategy         = var.cloud_guard ? { drain_timeout = "60s" } : null
  version          = "1.35"
  template = {
    resources          = { platform = local.gpu_shape.platform, preset = local.gpu_shape.local_preset }
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
  depends_on       = [nebius_iam_v1_group_membership.guard, nebius_iam_v1_access_permit.guard]
  fixed_node_count = 1
  strategy         = var.cloud_guard ? { drain_timeout = "60s" } : null
  template = {
    service_account_id = try(nebius_iam_v1_service_account.guard[0].id, null)
    resources          = { platform = "cpu-d3", preset = "16vcpu-64gb" }
    boot_disk          = { type = "NETWORK_SSD", size_gibibytes = 64 }
    network_interfaces = [{ subnet_id = var.subnet_id }]
  }

}

resource "nebius_mk8s_v1_node_group" "remote" {
  count            = var.ipc_diagnostic_only ? 0 : 1
  parent_id        = nebius_mk8s_v1_cluster.topology.id
  name             = "router-remote"
  fixed_node_count = 1
  strategy         = var.cloud_guard ? { drain_timeout = "60s" } : null
  version          = "1.35"
  template = {
    resources          = { platform = local.gpu_shape.platform, preset = local.gpu_shape.local_preset }
    gpu_cluster        = { id = nebius_compute_v1_gpu_cluster.local.id }
    gpu_settings       = { drivers_preset = "cuda13.0" }
    boot_disk          = { type = "NETWORK_SSD", size_gibibytes = 256 }
    network_interfaces = [{ subnet_id = var.subnet_id }]
    preemptible        = var.gpu_preemptible ? {} : null
    reservation_policy = var.gpu_preemptible ? null : { policy = "FORBID" }
  }

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

resource "nebius_iam_v1_service_account" "guard" {
  count       = var.cloud_guard ? 1 : 0
  parent_id   = var.project_id
  name        = "router-session-cleanup"
  description = "Temporary identity for bounded experiment cleanup; removed with session."
}
resource "nebius_iam_v1_group" "guard" {
  count     = var.cloud_guard ? 1 : 0
  parent_id = var.project_id
  name      = "router-session-cleanup"
}
resource "nebius_iam_v1_group_membership" "guard" {
  count     = var.cloud_guard ? 1 : 0
  parent_id = nebius_iam_v1_group.guard[0].id
  member_id = nebius_iam_v1_service_account.guard[0].id
}
resource "nebius_iam_v1_access_permit" "guard" {
  count       = var.cloud_guard ? 1 : 0
  parent_id   = nebius_iam_v1_group.guard[0].id
  resource_id = var.project_id
  role        = "editor"
}
