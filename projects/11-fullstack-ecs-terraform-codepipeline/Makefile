.PHONY: init plan apply destroy fmt local build-api build-web pipeline logs
TF := terraform -chdir=infra

init:       ; $(TF) init
plan: init  ; $(TF) plan
apply: init ; $(TF) apply
destroy:    ; $(TF) destroy
fmt:        ; terraform fmt -recursive infra

local:      ; docker compose up --build

build-api:
	docker build -t fullstack-api ./app/api
build-web:
	docker build -t fullstack-web ./app/web

pipeline:
	aws codepipeline start-pipeline-execution --name "$$($(TF) output -raw pipeline_name)"

logs:
	aws logs tail "/ecs/$$($(TF) output -raw cluster_name)/api" --follow --format short
