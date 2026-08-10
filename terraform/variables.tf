variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "asia-northeast1"
}

variable "billing_account" {
  type        = string
  description = "Billing account id for the budget alert."
}

variable "budget_jpy" {
  type    = number
  default = 100000
}

variable "db_tier" {
  type    = string
  default = "db-g1-small"
}

variable "github_repository" {
  type        = string
  description = "owner/repo allowed to deploy via Workload Identity Federation."
}

variable "iap_support_email" {
  type = string
}

variable "iap_members" {
  type        = list(string)
  description = "Google group(s) allowed through IAP, e.g. group:naxos@example.com."
  default     = []
}
