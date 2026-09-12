.PHONY: init plan apply destroy fmt test chat
TF := terraform -chdir=infra

init:       ; $(TF) init
plan: init  ; $(TF) plan
apply: init ; $(TF) apply
destroy:    ; $(TF) destroy
fmt:        ; terraform fmt -recursive infra
test:       ; python -m pytest -q

chat:
	@aws lexv2-runtime recognize-text \
	  --bot-id "$$($(TF) output -raw bot_id)" \
	  --bot-alias-id "$$($(TF) output -raw bot_alias_id)" \
	  --locale-id en_US --session-id cli-session \
	  --text "$(TEXT)"
