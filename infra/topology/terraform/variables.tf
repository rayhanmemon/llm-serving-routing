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
