variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "asia-northeast1"
}

variable "billing_account" {
  type        = string
  description = "Billing account id for the budget alert. Empty skips the budget."
  default     = ""
}

variable "budget_jpy" {
  type    = number
  default = 100000
}

variable "db_tier" {
  type        = string
  description = "db-f1-micro is enough for the working group; db-g1-small when latency matters."
  default     = "db-f1-micro"
}

variable "github_repository" {
  type        = string
  description = "owner/repo allowed to deploy via WIF. Empty skips the CI identity."
  default     = ""
}

variable "iap_members" {
  type        = list(string)
  description = "Google group(s) allowed through IAP, e.g. group:naxos@example.com. Empty leaves the API IAM-only."
  default     = []
}
