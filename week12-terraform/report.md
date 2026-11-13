# Step 9: What Would Change If the Advisor Ran on AWS Lambda?

## Comparison Between EC2 and Lambda

If the advisor application were moved from EC2 to AWS Lambda, several infrastructure resources would no longer be needed. AWS would manage most of the server-related tasks, while Terraform would still manage the remaining AWS resources.

| Advisor on EC2 (this lab) | Advisor on Lambda | Effort moved to |
|---|---|---|
| **Launch template and user-data:** install and configure the application. | Replaced with an `aws_lambda_function` containing the zipped application code. | Lambda |
| **Auto Scaling Group and instance refresh:** maintains one EC2 instance and replaces it when updated. | No ASG is needed because Lambda scales automatically based on requests. | Lambda |
| **App security group rule for port 3000:** allows traffic from the ALB. | No application port needs to be opened because the ALB invokes Lambda directly. | Lambda |
| **Instance profile and role:** provide permission to read the Gemini key. | The instance profile is replaced by a Lambda execution role with the same permission. | Terraform |
| **Node.js install and systemd service:** user data installs Node.js and starts the application. | AWS provides the managed Node.js runtime. | Lambda |
| **Health check and grace period:** allow time for the EC2 instance to start. | No instance boot process is required. | Lambda |

## Q1. Which Terraform Resources Would Disappear?

The resources that exist mainly because the advisor runs on EC2 would no longer be necessary. These include the advisor launch template (`aws_launch_template.tier["advisor"]`), Auto Scaling Group (`aws_autoscaling_group.tier["advisor"]`), and instance profile (`aws_iam_instance_profile.tier["advisor"]`).

The advisor's user-data configuration in `user-data.sh.tftpl` would also disappear because Lambda does not need an EC2 boot script to install and start the application.

The advisor would also no longer need the app security group rules (`app_from_alb`, `alb_to_app`, `app_out` and `app_ssh`). However, these rules would stay in the Terraform code, because the weather tier still runs on EC2 and uses the same security group.

## Q2. What Would Stay the Same?

Some existing resources would still be required. The Gemini parameter in AWS Systems Manager Parameter Store would remain because the application still needs the API key.

The IAM role would also remain, but it would become a Lambda execution role instead of an EC2 role. The advisor's existing policy would be attached to this role to allow access to the Gemini parameter.

The Application Load Balancer, its security group, and the advisor listener on port 8081 would also remain. The entire weather tier would stay unchanged because it would continue running on EC2.

## Q3. What New Resources or Changes Would Be Needed?

First, I would create an `aws_lambda_function` resource containing the zipped advisor application code. I would also need an `aws_lambda_permission` resource to allow the Application Load Balancer to invoke the function.

The advisor target group would need to change its target type from `instance` to `lambda`. An `aws_lb_target_group_attachment` would then connect the Lambda function to the target group.

Finally, the application code in `app.js` would need to be modified. Instead of running an Express server using `app.listen`, it would need to receive an ALB event and return a response object that the load balancer understands.

## Q4. Who Removes the Work, What Are the Benefits, and What New Problems Could Arise?

Lambda removes most of the server management work. AWS takes care of running the servers, maintaining the operating system, providing the Node.js runtime, scaling the application, and managing instance replacement. It also removes the need for an EC2 security group rule for application traffic.

Terraform still manages the infrastructure, but there would be fewer resources to configure. Terraform also handles resource creation order, deletion, and drift detection without requiring separate scripts.

The main benefits are that there are no servers to maintain or patch, the application scales automatically, and billing is based on usage rather than keeping an EC2 instance running when nobody is using it.

However, Lambda also introduces some challenges:

- **Cold starts:** The first request after a period of inactivity may take longer because Lambda needs to initialize the function. This can include retrieving the Gemini key from Parameter Store.
- **Time and size limits:** Lambda has execution time limits, and ALB requests and responses have a 1 MB size limit.
- **Testing difficulties:** Testing would be different because I could no longer simply run `node app.js` and use curl. I would need to simulate ALB events or deploy the function to AWS. Application logs would also be handled through CloudWatch.

Overall, moving the advisor to Lambda would reduce the amount of infrastructure I need to manage. AWS would handle most of the server-related responsibilities, while Terraform would continue managing the resources that remain. The trade-off is less server maintenance but new considerations around cold starts, execution limits, and testing.



--------------------------------------------------------------------------------------------------
# Short Answers and Review Questions

## How Terraform Is Like AWS Lambda, and Where the Comparison Stops

Terraform and AWS Lambda share one design idea: I hand over a small, self-contained piece, and the platform does the repetitive, error-prone work around it. With Lambda, I supply a function and AWS handles the servers, patching, scaling and health. With Terraform, I supply a description of the end state and Terraform handles the call order, waiting, retries and comparing before and after. Both run on demand and then stop, both should be idempotent (a retried event or a second `apply` changes nothing), and both act through an IAM identity where least privilege applies.

The comparison stops at what each one is. Lambda is a runtime service that runs my application code every time a request arrives. Terraform is a deployment tool that runs before the application exists and never handles a request. Lambda keeps nothing between invocations, while Terraform keeps a state file that records everything it built, and losing or leaking that file is a serious problem. Lambda bills per invocation, while Terraform is free but creates resources that cost money.

## What Terraform Did Not Make Easier

- **Secrets:** Storing the real Gemini key was still a one-time CLI command. Even with `ignore_changes`, my real key ended up in `terraform.tfstate` after a later `apply` refreshed the parameter, so the state file itself became a secret to protect.
- **Line count:** The Terraform was not dramatically shorter than a bash version (the guide's reference is about 420 lines of bash vs my 420 lines of Terraform). My lab06 Python was 1,436 lines, but much of that was docstrings, error handling and RDS code. The saving was in kinds of work, not lines.
- **Debugging the instances:** When something goes wrong at boot, I still SSH in and read `journalctl`. Terraform only knows that the resource exists, not whether the app inside it works.
- **New things to learn and protect:** HCL, the plan/apply workflow, and the state file.
- **Speed of mistakes:** One `terraform destroy` removes all 25 resources at once, so reading the plan matters.

## Section 13.1 Review Questions

**Declarative and imperative.** The imperative bash/Python version can easily do one-time or conditional actions, such as storing a secret, testing something and branching on the result, or SSHing in to restart a service, which are awkward in Terraform. Terraform did things my scripts never did: it showed a plan before changing anything, detected drift (a changed health check path and a deleted listener), replaced an instance automatically with an instance refresh, and deleted everything with one command.

**State file.** If I lost the state, Terraform would forget what it built: the next `apply` would try to create everything again and fail on name conflicts (or create duplicates), and the old resources would have to be imported or deleted by hand. If two people applied from different copies, each would overwrite the other's changes or even destroy resources the other created. Teams solve this with remote state in a shared, encrypted place (such as an S3 bucket) with locking, so only one person can change it at a time.

**Secrets in state.** Any attribute Terraform reads from AWS can reach the state, which I saw myself: the provider refreshed my SSM parameter with decryption and stored the real key. Database passwords, generated passwords, outputs and data sources that read secrets can all end up there. A plan file is also sensitive because it contains the full planned values, including secrets, so it must be protected or deleted like the state.

**Plan before apply.** Reading the plan protects me from unintended changes, especially deleting or replacing resources I did not mean to touch. The symbol that worries me most is `-/+`, because it means destroy and recreate: in Part A, changing a table's key would have deleted the table and all its data.

**Version pinning.** With no pin, a new major provider version could change defaults or rename arguments, and the same code could suddenly behave differently or fail. We commit `.terraform.lock.hcl` so that everyone, and every pipeline, uses exactly the same provider version and checksums.

**for_each or count.** With `for_each`, each copy is named by its key (`tier["weather"]`), so removing one tier from the map deletes only that tier's resources. With `count`, copies are numbered, so removing one in the middle shifts the numbers, and Terraform would destroy and recreate the others.

**Ownership.** When a tool and a person both edit the same resource, the person's change becomes drift that the next `apply` silently reverts, or the tool's assumptions break and causes an outage. In my drift test, one manual change to a health check path made the target unhealthy, and the Auto Scaling Group replaced the whole instance within about a minute.

**Drift.** A team can run `terraform plan -detailed-exitcode` on a schedule (for example, daily in a CI pipeline) and alert when it finds differences, or use AWS Config. Changes by hand should be limited to emergency "break-glass" access by authorized administrators, and every such change should be written back into the Terraform code afterwards.

**Blast radius.** How much a change touches is decided by the dependency graph: only resources that reference the changed value are updated. Changing `cache_seconds` touched only the weather launch template and its Auto Scaling Group. It could be shrunk further by splitting the stack into separate states or modules per tier, so one change can never reach the other tier.

**Least privilege.** A reference such as `aws_ssm_parameter.gemini.arn` always resolves to the exact ARN of the real resource, including the account, region and name, and it updates automatically if the name changes. A typed-in ARN can contain a typo or go out of date, which causes confusing `AccessDenied` errors or, worse, grants access to the wrong resource.

**Terraform and Lambda.** Both let me hand over a small unit and let the platform do the repetitive work around it. The difference that matters most is that Lambda runs my application at request time, while Terraform runs only when I deploy and keeps a state file that must be protected.

**Destroy.** In a real environment I would protect anything holding data that cannot be recreated: databases, DynamoDB tables with real data, S3 buckets, and secrets. I would add `lifecycle { prevent_destroy = true }` to those resources so that Terraform refuses to destroy them, and also enable deletion protection where AWS offers it, such as on RDS.

----------------------------------------------------------------------------------------------------

# Experiment Results and Sentences

## Item 2: Part A, the `-/+` Plan

The `-/+` plan is the one to be careful about because it means Terraform will delete the existing table, and every item stored in it, before creating a new empty one. Changing the table's key looks like a small edit in the file, but the plan revealed that it would destroy data.

## Item 5: Steps 1 to 3

### Step 1: Effort Table

| | lab06 (Python) | lab07 (Terraform) |
|---|---|---|
| Lines to create the stack | 1,247 | 420 |
| Lines to delete the stack | 189 | 0 |
| Steps you had to order by hand | 12 | 0 |
| Helper functions you wrote | 91 | 0 |
| Retry or wait loops you wrote | 13 | 0 |

My lab06 was the graduate Python version, so I compared Python (1,436 lines in total) against Terraform (420 lines). The guide's reference bash version of lab06 is about 420 lines, roughly the same as Terraform. My Python was longer because it included docstrings, specific error handling and the RDS database code, and the helper function count includes every function in those files.

Even where the line counts are similar, the effort went down because Terraform removed whole kinds of work: there is no delete code, no hand-ordered steps, and no retry or wait loops, because `terraform destroy`, the dependency graph and the provider handle them. The work I still wrote is a description of what should exist, not instructions for how to build and remove it.

### Step 2: The Order Terraform Worked Out

If I removed the `WEATHER_URL` line from `compute.tf`, the advisor's launch template would no longer reference `aws_lb.main`, so Terraform could build them in parallel and the order would change. The advisor would still boot, but with no `WEATHER_URL`, every request would fail with `502 weather_service_unavailable`.

### Step 3: Running Apply Again

Running my lab06 create script twice only checked whether each resource existed by name and printed "already exists, skipping", without checking that its settings were still correct. A second `terraform apply` compares every attribute with the real infrastructure, so `No changes` means everything still matches, not just that it exists.

## Item 6: Steps 4 and 5

### Step 4: Rolling Replacement

Changing `cache_seconds` took 2 commands in lab07: adding one line to `terraform.tfvars` and running `terraform apply`. The plan changed only the weather launch template and its Auto Scaling Group, and the instance refresh replaced the instance automatically (status `Successful 100`), with `CACHE_SECONDS=60` confirmed on the new instance. In lab06 the same change took about 4 to 5 steps: edit `.env`, re-run the create script to publish a new template version, find the instance, terminate it by hand, and wait for the replacement.

### Step 5: Drift

When I re-ran my lab06 create script, its `ensure_target_group` function found the target group by name and printed "already exists, skipping" without checking that its health check path was still correct, so the broken `/nope` setting would have stayed in place. `terraform plan` compared every attribute against the real resource, reported the drift (`path = "/nope" -> "/"`) and a missing listener (`1 to add`), and one `apply` repaired both. I also saw the cost of drift: while the health check pointed at `/nope`, the target was unhealthy, and because the Auto Scaling Group uses ELB health checks, it replaced the whole instance within about a minute.

## Item 7: Steps 6 and 7

### Step 6: Least Privilege

An attacker who takes over the advisor tier gets only what the advisor role allows: reading the one Gemini key parameter. They cannot read, write or scan the weather table, and they cannot read any other parameter, because the policy names exactly one resource. The IAM policy simulator confirmed this: each role was `allowed` on its own resource and `implicitDeny` on the other's.

### Step 7: The Key and the State File

With `ignore_changes` removed, the next `terraform apply` would see that the real key differs from `not-set-yet` in the code and would overwrite it back to the placeholder, wiping my key and sending every advice request to the fallback. The state would then hold `not-set-yet`.

However, my test showed that `ignore_changes` only prevents the plan from changing the value. Each `plan` or `apply` still refreshes the state from AWS, and because the provider reads the parameter with decryption, my real key ended up in `terraform.tfstate` after the later applies (the check found a 53-character value instead of the placeholder). The state file must therefore be treated as a secret and kept out of Git (my `.gitignore` excludes `*.tfstate`), or the parameter should use Terraform's write-only `value_wo` argument so the value is never stored. After `destroy`, I also deleted `terraform.tfstate.backup`, which still contained the key.

## Item 8: Step 8, Silent Fallback

With a wrong key, every advice answer said `fallback` and nothing returned an error; the only evidence was `gemini_error` lines with status 400 in the advisor's log. A design where users never see an error and the only evidence is a log line nobody reads can hide a broken dependency for days: everything looks fine, but users quietly get worse, rule-based answers instead of AI advice, and no one fixes the wrong or expired key until someone happens to check the logs. It needs monitoring or an alarm on `gemini_error` lines, or at least a visible "source: fallback" label that someone watches.

## Item 10: Step 10, Destroy and Cost

One `terraform destroy` removed all 25 resources (`Destroy complete! Resources: 25 destroyed.`). It replaced my lab06 delete scripts, their ordered steps and their retry loops. The verification commands showed no Terraform state, load balancers, Auto Scaling Groups, instances, DynamoDB tables, SSM parameters or lab roles left. The tagging API briefly still listed six security group rules, but `describe-security-group-rules` returned `InvalidSecurityGroupRuleId.NotFound`, proving they no longer existed and the tag index was only lagging.

**Cost estimate:** the stack ran from about 13:35 to 16:15, about 2.7 hours. At about $0.07 per hour, the estimated cost is about **$0.19**, well under the $1.00 budget.

