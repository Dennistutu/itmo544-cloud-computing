# Lab 03 — Configuring AWS CLI and GitHub Inside a Docker Container

Course: ITMO 544 — Cloud Computing
Student: Dennis Osei Tutu
Email: doseitutu@hawk.illinoistech.edu

## Summary

This lab tied together the previous two labs by turning the Ubuntu Docker container into a working cloud development environment. Inside the container, I installed the AWS CLI v2 and configured it with the IAM lab user's access key from Lab 01, installed and configured Git with my college identity, authenticated to GitHub using a Personal Access Token (PAT), and cloned this repository. I then wrote a simple Bash script (`list_buckets.sh`) that uses the AWS CLI to list S3 buckets, and pushed it back to GitHub — completing an end-to-end workflow from local container to cloud provider to version control.


## The `list_buckets.sh` Script

The script lives at the root of this repository:

```bash
#!/bin/bash
echo "Listing S3 buckets in your AWS account:"
aws s3 ls
```

When run inside the container, it uses the AWS CLI credentials configured in Part D to query the S3 service. The bucket list was empty because no S3 buckets have been created yet, which is expected at this stage.

## Why Credentials Should Never Be Committed

You should never commit AWS credentials or GitHub tokens to a repository — even a private one — because credentials in source code are one of the easiest ways to lose control of an account. A private repo is not a safe vault: collaborators or future clones could unintentionally expose it. More importantly, Git preserves history — once a secret is committed, deleting the file in a later commit does not remove it, and rotating the credential becomes the only real fix. A leaked AWS key can be used to spin up expensive resources or exfiltrate data from S3, and a leaked GitHub PAT can be used to modify or delete repositories. Safer approaches include keeping credentials in files ignored by Git (via `.gitignore`), using environment variables, or storing them in secret managers like AWS Secrets Manager or GitHub Actions Secrets — which is exactly why my AWS keys live in `~/.aws/credentials` and my PAT is only cached in memory during this lab.