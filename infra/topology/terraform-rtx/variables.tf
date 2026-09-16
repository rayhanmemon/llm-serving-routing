variable "project_id" {
  type        = string
  description = "Nebius project in uk-south2."
}

variable "subnet_id" {
  type        = string
  description = "Existing uk-south2 subnet in that project."
}

variable "nebius_profile" {
  type    = string
  default = "default"
}

variable "gpu_platform" {
  type        = string
  default     = "RTX6000-A"
  description = "Logical GPU platform recorded by the guarded session."

  validation {
    condition     = var.gpu_platform == "RTX6000-A"
    error_message = "gpu_platform must be RTX6000-A."
  }
}

variable "infiniband_fabric" {
  type        = string
  default     = ""
  description = "Empty because RTX PRO 6000 nodes are not GPU-cluster compatible."

  validation {
    condition     = var.infiniband_fabric == ""
    error_message = "infiniband_fabric must remain empty for RTX PRO 6000."
  }
}

variable "gpu_preemptible" {
  type        = bool
  default     = false
  description = "Must remain false for the priced on-demand RTX session."
}

variable "ipc_diagnostic_only" {
  type        = bool
  default     = false
  description = "Must remain false for the four-resource serving topology."
}
