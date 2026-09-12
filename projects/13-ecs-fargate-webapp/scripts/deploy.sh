#!/usr/bin/env bash
# Register a new task definition revision and start a blue/green deployment.
#
#   ./scripts/deploy.sh                      # latest image tag in ECR
#   IMAGE_TAG=sha-abc1234 ./scripts/deploy.sh
set -euo pipefail

cd "$(dirname "$0")/.."
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

TF="terraform -chdir=infra"
REGION="$(aws configure get region)"
CLUSTER="$($TF output -raw cluster_name)"
SERVICE="$($TF output -raw service_name)"
FAMILY="$($TF output -raw task_definition_family)"
REPOSITORY_URI="$($TF output -raw ecr_repository_url)"
CONTAINER_NAME="$($TF output -raw container_name)"
CONTAINER_PORT="$($TF output -raw container_port)"
APP_NAME="$($TF output -raw codedeploy_app_name)"
DEPLOYMENT_GROUP="$($TF output -raw codedeploy_deployment_group)"

if [[ -z "${IMAGE_TAG:-}" ]]; then
  IMAGE_TAG="$(aws ecr describe-images \
    --repository-name "$(basename "$REPOSITORY_URI")" \
    --query 'sort_by(imageDetails,& imagePushedAt)[-1].imageTags[0]' --output text)"
  echo "no IMAGE_TAG given; using the most recently pushed: $IMAGE_TAG"
fi

IMAGE="$REPOSITORY_URI:$IMAGE_TAG"
echo "deploying $IMAGE to $SERVICE"

# Take the current definition, swap the image, register the new revision.
# Stripping the read-only fields is required or RegisterTaskDefinition rejects it.
CURRENT="$(aws ecs describe-task-definition --task-definition "$FAMILY" --query taskDefinition)"

NEW_DEFINITION="$(echo "$CURRENT" | jq --arg IMAGE "$IMAGE" --arg NAME "$CONTAINER_NAME" '
  .containerDefinitions = (.containerDefinitions | map(if .name == $NAME then .image = $IMAGE else . end))
  | del(.taskDefinitionArn, .revision, .status, .requiresAttributes,
        .compatibilities, .registeredAt, .registeredBy, .deregisteredAt)
')"

TASK_DEFINITION_ARN="$(aws ecs register-task-definition \
  --cli-input-json "$NEW_DEFINITION" \
  --query 'taskDefinition.taskDefinitionArn' --output text)"

echo "registered $TASK_DEFINITION_ARN"

APPSPEC="$(jq -n \
  --arg TD "$TASK_DEFINITION_ARN" \
  --arg NAME "$CONTAINER_NAME" \
  --argjson PORT "$CONTAINER_PORT" '
{
  version: 0.0,
  Resources: [{
    TargetService: {
      Type: "AWS::ECS::Service",
      Properties: {
        TaskDefinition: $TD,
        LoadBalancerInfo: { ContainerName: $NAME, ContainerPort: $PORT }
      }
    }
  }]
}')"

DEPLOYMENT_ID="$(aws deploy create-deployment \
  --application-name "$APP_NAME" \
  --deployment-group-name "$DEPLOYMENT_GROUP" \
  --revision "revisionType=AppSpecContent,appSpecContent={content='$(echo "$APPSPEC" | jq -c .)'}" \
  --description "Deploy $IMAGE_TAG" \
  --query deploymentId --output text)"

echo "deployment $DEPLOYMENT_ID started"
echo "test the green fleet at: $($TF output -raw test_listener_url)/version"
echo

# Poll until the traffic shift finishes or the alarms roll it back.
while true; do
  STATUS="$(aws deploy get-deployment --deployment-id "$DEPLOYMENT_ID" \
    --query 'deploymentInfo.status' --output text)"
  echo "  status: $STATUS"

  case "$STATUS" in
    Succeeded) echo "deployment succeeded"; exit 0 ;;
    Failed|Stopped)
      aws deploy get-deployment --deployment-id "$DEPLOYMENT_ID" \
        --query 'deploymentInfo.errorInformation' --output json
      echo "deployment did not succeed" >&2
      exit 1 ;;
  esac

  sleep 15
done
