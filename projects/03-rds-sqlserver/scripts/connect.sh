#!/usr/bin/env bash
# Port-forward RDS SQL Server to 127.0.0.1:1433 through an SSM-managed instance.
#
# Requires: an EC2 instance in the private subnets with the SSM agent and the
# AmazonSSMManagedInstanceCore role. Pass its instance ID as $1, or set INSTANCE_ID.
set -euo pipefail

cd "$(dirname "$0")/.."

INSTANCE_ID="${1:-${INSTANCE_ID:-}}"
LOCAL_PORT="${LOCAL_PORT:-1433}"

if [[ -z "$INSTANCE_ID" ]]; then
  echo "usage: $0 <ssm-managed-instance-id>   (or set INSTANCE_ID)" >&2
  echo "hint: aws ssm describe-instance-information --query 'InstanceInformationList[].InstanceId'" >&2
  exit 1
fi

DB_HOST="$(terraform -chdir=infra output -raw db_address)"

echo "Forwarding 127.0.0.1:${LOCAL_PORT} -> ${DB_HOST}:1433 via ${INSTANCE_ID}"
echo "Press Ctrl-C to stop."

aws ssm start-session \
  --target "$INSTANCE_ID" \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "{\"host\":[\"${DB_HOST}\"],\"portNumber\":[\"1433\"],\"localPortNumber\":[\"${LOCAL_PORT}\"]}"
