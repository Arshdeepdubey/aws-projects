.PHONY: init plan apply destroy fmt connect password
TF := terraform -chdir=infra

init:       ; $(TF) init
plan: init  ; $(TF) plan
apply: init ; $(TF) apply
destroy:    ; $(TF) destroy
fmt:        ; terraform fmt -recursive infra
connect:    ; ./scripts/connect.sh
password:
	@aws secretsmanager get-secret-value \
	  --secret-id "$$($(TF) output -raw secret_name)" \
	  --query SecretString --output text
