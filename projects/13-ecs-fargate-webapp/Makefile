.PHONY: init plan apply destroy fmt build push deploy rollback logs test local
TF := terraform -chdir=infra

init:       ; $(TF) init
plan: init  ; $(TF) plan
apply: init ; $(TF) apply
destroy:    ; $(TF) destroy
fmt:        ; terraform fmt -recursive infra

build:      ; docker build -t fargate-web ./app
local: build ; docker run --rm -p 8080:8080 fargate-web
test:       ; cd app && npm test

push:       ; ./scripts/build_and_push.sh
deploy:     ; ./scripts/deploy.sh
rollback:   ; ./scripts/rollback.sh

logs:
	aws logs tail "/ecs/$$($(TF) output -raw cluster_name)" --follow --format short
