#!/usr/bin/env bash
# Stop the in-flight deployment and shift traffic back to the previous task set.
set -euo pipefail

cd "$(dirname "$0")/.."

TF="terraform -chdir=infra"
APP_NAME="$($TF output -raw codedeploy_app_name)"
DEPLOYMENT_GROUP="$($TF output -raw codedeploy_deployment_group)"

DEPLOYMENT_ID="${1:-$(aws deploy list-deployments \
  --application-name "$APP_NAME" \
  --deployment-group-name "$DEPLOYMENT_GROUP" \
  --include-only-statuses InProgress Ready Created Queued \
  --query 'deployments[0]' --output text)}"

if [[ -z "$DEPLOYMENT_ID" || "$DEPLOYMENT_ID" == "None" ]]; then
  echo "No deployment in progress." >&2
  echo "To roll back a completed deployment, redeploy the previous image:" >&2
  echo "  IMAGE_TAG=sha-<previous> ./scripts/deploy.sh" >&2
  exit 1
fi

echo "stopping $DEPLOYMENT_ID and rolling traffic back"
aws deploy stop-deployment --deployment-id "$DEPLOYMENT_ID" --auto-rollback-enabled

aws deploy get-deployment --deployment-id "$DEPLOYMENT_ID" \
  --query 'deploymentInfo.{status:status,rollback:rollbackInfo}' --output json
