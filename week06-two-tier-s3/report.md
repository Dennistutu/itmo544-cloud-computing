## Why Separate IAM Roles?

The uploader and viewer use separate IAM roles because each instance only needs the minimum permissions to perform its task. This enforces the principle of least privilege. The uploader only writes “shared.txt” (“s3:PutObject”), while the viewer only reads it (“s3:GetObject”, plus “s3:ListBucket” on the bucket). If there is any compromise of an instance by an attacker, the damage is limited to the single action that the instance needs, and the other instance is not affected.

## Why Embedding Code in User-Data Needs No Credential

The app code is placed directly inside the user-data, so the instance already has its files when it starts and does not need to download anything from Git or S3. Because of this, the instance never needs a Git or S3 credential just to get its own code, which keeps it more secure.
