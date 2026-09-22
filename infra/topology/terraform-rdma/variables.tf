variable "project_id" {
  type        = string
  description = "Nebius project for the RDMA qualification."
}
variable "subnet_id" {
  type        = string
  description = "Existing subnet in the selected project."
}
variable "nebius_profile" {
  type    = string
  default = "default"
}

variable "gpu_platform" {
  type        = string
  default     = "H100"
  description = "GPU platform for both local and remote node groups."

  validation {
    condition     = contains(["H100", "H200"], var.gpu_platform)
    error_message = "gpu_platform must be H100 or H200."
  }
}

variable "infiniband_fabric" {
  type        = string
  default     = "fabric-6"
  description = "InfiniBand fabric for the local eight-GPU node only. The single-GPU remote node cannot join a GPU cluster."
}

variable "gpu_preemptible" {
  type        = bool
  default     = true
  description = "Use preemptible GPU nodes. Changing this requires the priced session decision."
}

variable "ipc_diagnostic_only" {
  type        = bool
  default     = false
  description = "Create only the eight-GPU local node for the bounded CUDA IPC diagnostic."
}

variable "cloud_guard" {
  type        = bool
  default     = false
  description = "Attach a dedicated experiment-project cleanup identity to the CPU node."
}

variable "single_gpu_host" {
  type        = bool
  default     = false
  description = "Keep the CPU cleanup/client node, but omit the remote GPU node."
}
variable "existing_guard_service_account_id" {
  type        = string
  default     = ""
  description = "Pre-authorized project cleanup identity; no session IAM resources when supplied."
}
