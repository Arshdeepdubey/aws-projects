.PHONY: init plan apply deploy destroy run fmt
TF := terraform -chdir=infra

init:        ; $(TF) init
plan: init   ; $(TF) plan
apply: init  ; $(TF) apply
deploy: apply
	@echo "URL: $$($(TF) output -raw environment_url)"
destroy:     ; $(TF) destroy
fmt:         ; terraform fmt -recursive infra

run:
	cd app && python -m venv .venv && . .venv/bin/activate && \
	pip install -q -r requirements.txt && flask --app application run --debug
