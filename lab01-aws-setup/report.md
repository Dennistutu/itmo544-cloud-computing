# Lab 01 — AWS Account, IAM User, and Access Keys

Summary

This lab covered securing the AWS root user with MFA, creating a dedicated IAM user for course work (`itmo544-lab-user`) with least-privilege permissions, enabling MFA on that IAM user, and generating an access key for programmatic access via the AWS CLI.



## Access Key Confirmation

Access key created successfully for `itmo544-lab-user`.

Access Key ID:** `AKIA****...XXTR
Secret Access Key:** Downloaded via `.csv` file and saved securely (not shared in this repository).

## Reflection — Why Root MFA and Least-Privilege IAM Matter

Root user MFA and least-privilege IAM users are two of the most important security controls in any AWS account. The root user has unrestricted power over the entire account — it can access billing, delete resources, and shut everything down. If someone steals the root password, they own the account. MFA adds a second layer of defense: even if the password is leaked, an attacker still needs the code from my authenticator app to log in.

The least-privilege principle protects the account in a different way. Instead of doing daily work as the all-powerful root user, I use an IAM user with only the permissions I actually need for my labs (EC2, S3, DynamoDB, and a few others). If this IAM user's credentials are ever exposed — for example, if I accidentally push an access key to GitHub — the damage is limited to what those specific permissions allow. The attacker cannot touch billing, delete the account, or create new admin users outside that scope.

Together, these two practices reduce both the chance of a breach (MFA) and the impact of one (least privilege). This is the same defense-in-depth thinking used in enterprise network security, and it is why AWS strongly recommends locking down the root user and never using it for routine work.