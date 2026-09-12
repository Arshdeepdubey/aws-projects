.PHONY: init plan apply deploy sync invalidate destroy fmt
TF := terraform -chdir=infra

init:       ; $(TF) init
plan: init  ; $(TF) plan
apply: init ; $(TF) apply
fmt:        ; terraform fmt -recursive infra

sync:
	aws s3 sync site/ "s3://$$($(TF) output -raw bucket_name)/" --delete

invalidate:
	aws cloudfront create-invalidation \
	  --distribution-id "$$($(TF) output -raw distribution_id)" --paths '/*'

deploy: apply sync invalidate
	@echo "Live at: $$($(TF) output -raw site_url)"

destroy: ; $(TF) destroy
