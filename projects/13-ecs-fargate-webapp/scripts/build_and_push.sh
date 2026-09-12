#!/usr/bin/env bash
# Build the app image, tag it with the git SHA, and push it to ECR.
#
#   ./scripts/build_and_push.sh            # tag from git
#   IMAGE_TAG=sha-abc1234 ./scripts/build_and_push.sh
set -euo pipefail

cd "$(dirname "$0")/.."

REGION="$(terraform -chdir=infra output -raw aws_region 2>/dev/null || aws configure get region)"
REPOSITORY_URI="$(terraform -chdir=infra output -raw ecr_repository_url)"
ACCOUNT_ID="${REPOSITORY_URI%%.*}"

GIT_SHA="$(git rev-parse --short=7 HEAD 2>/dev/null || echo unknown)"
DIRTY=""
if ! git diff --quiet 2>/dev/null; then DIRTY="-dirty"; fi
IMAGE_TAG="${IMAGE_TAG:-sha-${GIT_SHA}${DIRTY}}"
BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "repository : $REPOSITORY_URI"
echo "tag        : $IMAGE_TAG"

if [[ -n "$DIRTY" ]]; then
  echo "warning: working tree is dirty — this image does not match any commit" >&2
fi

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# ECR repositories here are IMMUTABLE, so pushing an existing tag fails loudly
# rather than silently changing what that tag means.
if aws ecr describe-images --region "$REGION" \
     --repository-name "$(basename "$REPOSITORY_URI")" \
     --image-ids "imageTag=$IMAGE_TAG" >/dev/null 2>&1; then
  echo "image $IMAGE_TAG already exists — commit your changes or set IMAGE_TAG" >&2
  exit 1
fi

docker build \
  --platform linux/amd64 \
  --build-arg "IMAGE_TAG=$IMAGE_TAG" \
  --build-arg "GIT_SHA=$GIT_SHA" \
  --build-arg "BUILT_AT=$BUILT_AT" \
  -t "$REPOSITORY_URI:$IMAGE_TAG" \
  ./app

docker push "$REPOSITORY_URI:$IMAGE_TAG"

echo
echo "pushed $REPOSITORY_URI:$IMAGE_TAG"
echo "deploy it: IMAGE_TAG=$IMAGE_TAG ./scripts/deploy.sh"
