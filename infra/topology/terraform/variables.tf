variable "project_id" {
  type        = string
  description = "Nebius project in eu-north1."
}
variable "subnet_id" {
  type        = string
  description = "Existing subnet in that project."
}
variable "nebius_profile" {
  type    = string
  default = "default"
}

variable "infiniband_fabric" {
  type        = string
  default     = "fabric-6"
  description = "InfiniBand fabric for the local eight-GPU H100 node only. The single-GPU remote node cannot join a GPU cluster."
}

variable "gpu_preemptible" {
  type        = bool
  default     = true
  description = "Use preemptible GPU nodes. Changing this requires the priced session decision."
}
