#!/bin/bash
set -e
export $(grep -v '^#' .env | xargs)
unset VPC_ID SUBNET_A SUBNET_B ALB_SECURITY_GROUP_ID ALB_ARN ALB_DNS_NAME UPLOADER_TG_ARN VIEWER_TG_ARN
if [ -f stack_outputs.env ]; then
  export $(grep -v '^#' stack_outputs.env | xargs)
fi

# Fall back to lookups by name if stack_outputs.env was missing or incomplete
VPC_ID=${VPC_ID:-$(aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" \
  --query "Vpcs[0].VpcId" --output text --region "$AWS_REGION")}
ALB_ARN=${ALB_ARN:-$(aws elbv2 describe-load-balancers --names "$ALB_NAME" --region "$AWS_REGION" \
  --query "LoadBalancers[0].LoadBalancerArn" --output text 2>/dev/null || true)}
UPLOADER_TG_ARN=${UPLOADER_TG_ARN:-$(aws elbv2 describe-target-groups --names "$UPLOADER_TG_NAME" --region "$AWS_REGION" \
  --query "TargetGroups[0].TargetGroupArn" --output text 2>/dev/null || true)}
VIEWER_TG_ARN=${VIEWER_TG_ARN:-$(aws elbv2 describe-target-groups --names "$VIEWER_TG_NAME" --region "$AWS_REGION" \
  --query "TargetGroups[0].TargetGroupArn" --output text 2>/dev/null || true)}
ALB_SECURITY_GROUP_ID=${ALB_SECURITY_GROUP_ID:-$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=$ALB_SG_NAME" "Name=vpc-id,Values=$VPC_ID" \
  --query "SecurityGroups[0].GroupId" --output text --region "$AWS_REGION" 2>/dev/null || true)}

is_set() { [ -n "$1" ] && [ "$1" != "None" ]; }

# --- New: tear down the ALB/ASG/multi-AZ layer first ---
echo "== Deleting Auto Scaling Groups (this also terminates their instances) =="
aws autoscaling delete-auto-scaling-group --auto-scaling-group-name "$UPLOADER_ASG_NAME" \
  --force-delete --region "$AWS_REGION" 2>/dev/null || true
aws autoscaling delete-auto-scaling-group --auto-scaling-group-name "$VIEWER_ASG_NAME" \
  --force-delete --region "$AWS_REGION" 2>/dev/null || true

echo "Waiting for both Auto Scaling Groups to be gone..."
for ASG_NAME in "$UPLOADER_ASG_NAME" "$VIEWER_ASG_NAME"; do
  GONE=no
  for i in $(seq 1 20); do
    EXISTING=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG_NAME" \
      --region "$AWS_REGION" --query "AutoScalingGroups[0].AutoScalingGroupName" --output text 2>/dev/null || true)
    if ! is_set "$EXISTING"; then
      echo "$ASG_NAME deleted."
      GONE=yes
      break
    fi
    sleep 15
  done
  if [ "$GONE" = "no" ]; then
    echo "WARNING: $ASG_NAME still exists after 5 minutes - check the EC2 console."
  fi
done

echo "== Safety sweep: any leftover uploader/viewer instances (by Name tag) =="
IDS=$(aws ec2 describe-instances --region "$AWS_REGION" \
  --filters "Name=tag:Name,Values=uploader-app,viewer-app" \
            "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query "Reservations[*].Instances[*].InstanceId" --output text)
if [ -n "$IDS" ]; then
  echo "Terminating leftover instances: $IDS"
  aws ec2 terminate-instances --instance-ids $IDS --region "$AWS_REGION" > /dev/null
  aws ec2 wait instance-terminated --instance-ids $IDS --region "$AWS_REGION"
else
  echo "No leftover instances."
fi

echo "== Deleting the load balancer =="
if is_set "$ALB_ARN"; then
  aws elbv2 delete-load-balancer --load-balancer-arn "$ALB_ARN" --region "$AWS_REGION" 2>/dev/null || true
  aws elbv2 wait load-balancers-deleted --load-balancer-arns "$ALB_ARN" --region "$AWS_REGION" 2>/dev/null || true
  echo "Load balancer deleted."
else
  echo "No load balancer found, skipping."
fi

echo "== Deleting target groups =="
if is_set "$UPLOADER_TG_ARN"; then
  aws elbv2 delete-target-group --target-group-arn "$UPLOADER_TG_ARN" --region "$AWS_REGION" 2>/dev/null || true
fi
if is_set "$VIEWER_TG_ARN"; then
  aws elbv2 delete-target-group --target-group-arn "$VIEWER_TG_ARN" --region "$AWS_REGION" 2>/dev/null || true
fi

echo "== Restoring the app security group to its previous-lab state =="
if is_set "$ALB_SECURITY_GROUP_ID"; then
  aws ec2 revoke-security-group-ingress --group-id "$APP_SECURITY_GROUP_ID" \
    --protocol tcp --port 3000 --source-group "$ALB_SECURITY_GROUP_ID" --region "$AWS_REGION" >/dev/null 2>&1 || true
fi
aws ec2 authorize-security-group-ingress --group-id "$APP_SECURITY_GROUP_ID" \
  --protocol tcp --port 3000 --cidr 0.0.0.0/0 --region "$AWS_REGION" >/dev/null 2>&1 || true

echo "== Deleting the ALB security group =="
if is_set "$ALB_SECURITY_GROUP_ID"; then
  DELETED=no
  for i in $(seq 1 12); do
    if aws ec2 delete-security-group --group-id "$ALB_SECURITY_GROUP_ID" --region "$AWS_REGION" 2>/dev/null; then
      echo "ALB security group deleted."
      DELETED=yes
      break
    fi
    echo "ALB security group still in use, retrying in 10s..."
    sleep 10
  done
  if [ "$DELETED" = "no" ]; then
    echo "WARNING: could not delete $ALB_SECURITY_GROUP_ID - re-run this script in a minute."
  fi
else
  echo "No ALB security group found, skipping."
fi

echo "== Deleting launch templates =="
aws ec2 delete-launch-template --launch-template-name "$UPLOADER_LT_NAME" --region "$AWS_REGION" >/dev/null 2>&1 || true
aws ec2 delete-launch-template --launch-template-name "$VIEWER_LT_NAME" --region "$AWS_REGION" >/dev/null 2>&1 || true

rm -f stack_outputs.env uploader-lt-data.json viewer-lt-data.json user-data-uploader.sh user-data-viewer.sh \
  uploader-policy.json viewer-policy.json trust-policy.json

# --- Unchanged from the previous lab: bucket + IAM teardown ---
echo "== Removing IAM instance profiles and roles =="
aws iam remove-role-from-instance-profile --instance-profile-name "$UPLOADER_PROFILE_NAME" \
  --role-name "$UPLOADER_ROLE_NAME" 2>/dev/null || true
aws iam delete-instance-profile --instance-profile-name "$UPLOADER_PROFILE_NAME" 2>/dev/null || true
aws iam delete-role-policy --role-name "$UPLOADER_ROLE_NAME" \
  --policy-name "${UPLOADER_ROLE_NAME}-policy" 2>/dev/null || true
aws iam delete-role --role-name "$UPLOADER_ROLE_NAME" 2>/dev/null || true

aws iam remove-role-from-instance-profile --instance-profile-name "$VIEWER_PROFILE_NAME" \
  --role-name "$VIEWER_ROLE_NAME" 2>/dev/null || true
aws iam delete-instance-profile --instance-profile-name "$VIEWER_PROFILE_NAME" 2>/dev/null || true
aws iam delete-role-policy --role-name "$VIEWER_ROLE_NAME" \
  --policy-name "${VIEWER_ROLE_NAME}-policy" 2>/dev/null || true
aws iam delete-role --role-name "$VIEWER_ROLE_NAME" 2>/dev/null || true

echo "== Emptying and deleting S3 bucket =="
aws s3 rm "s3://$BUCKET_NAME" --recursive --region "$AWS_REGION" 2>/dev/null || true
aws s3api delete-bucket --bucket "$BUCKET_NAME" --region "$AWS_REGION" 2>/dev/null || true

echo "Cleanup complete."
