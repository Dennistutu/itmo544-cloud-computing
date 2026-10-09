#!/bin/bash
set -e
export $(grep -v '^#' .env | xargs)

echo "== Finding all uploader/viewer instances (by Name tag) =="
IDS=$(aws ec2 describe-instances --region "$AWS_REGION" \
  --filters "Name=tag:Name,Values=uploader-app,viewer-app" \
            "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query "Reservations[*].Instances[*].InstanceId" --output text)

if [ -n "$IDS" ]; then
  echo "Terminating instances: $IDS"
  aws ec2 terminate-instances --instance-ids $IDS --region "$AWS_REGION" > /dev/null
  aws ec2 wait instance-terminated --instance-ids $IDS --region "$AWS_REGION"
  echo "Instances terminated."
else
  echo "No uploader/viewer instances found - skipping termination."
fi
rm -f uploader_instance_id.txt viewer_instance_id.txt

echo "== Deleting launch templates =="
aws ec2 delete-launch-template --launch-template-name "$UPLOADER_LT_NAME" --region "$AWS_REGION" >/dev/null 2>&1 || true
aws ec2 delete-launch-template --launch-template-name "$VIEWER_LT_NAME" --region "$AWS_REGION" >/dev/null 2>&1 || true

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
