terraform {
  backend "s3" {
    # Configure these values through GitHub Actions or a local backend config.
    # The bucket must be created before the first remote plan/apply.
    bucket       = ""
    key          = "tee-time-finder/terraform.tfstate"
    region       = ""
    use_lockfile = true
  }
}
