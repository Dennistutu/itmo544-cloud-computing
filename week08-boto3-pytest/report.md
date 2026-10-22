## 8: What Moto proved, what it could not prove, and which tests cover the gap?

Moto showed that my code calls AWS APIs in the correct order, creates resources without making duplicates when run again, safely repeats deletion, and tightens and restores security group rules. All 19 tests passed in about 17 seconds at no cost. However, Moto could not prove that EC2 instances actually boot, that the application responds to HTTP requests, that IAM permissions work correctly in real AWS, or how long real AWS operations take. For example, my first real delete failed with a 'ResourceInUse error' because the load balancer listeners were still being removed. I fixed it by retrying that specific error with 'retry_on'. Moto could not catch this because it deletes resources instantly. The 14 integration tests, including the AWS policy simulator and a real upload through the load balancer, and the two chaos tests, which measured recovery at 85–97 seconds, cover these gaps.


## 9: ignore_errors("EntityAlreadyExists") vs. 2>/dev/null || true

Using 2>/dev/null || true hides every error, including typing mistakes, permission problems, and name conflicts. This makes it difficult to know what went wrong. In contrast, ignore_errors("EntityAlreadyExists") ignores only the expected error when a resource already exists, while other errors remain visible. My ResourceInUse error showed why this matters: because it was not an expected error, the script stopped and showed me the problem, which I fixed with a targeted retry instead of silently leaving resources behind.

## 10: Challenge 5

Bash was shorter when checking whether an S3 bucket already existed because it used a simple four-line if statement, while Python needed a try/except block and an error-code check. However, Python provided better error handling because it could distinguish a 404 error, meaning the bucket name was available, from a 403 error, meaning another account already owns that bucket name. Bash treated both cases the same way.

