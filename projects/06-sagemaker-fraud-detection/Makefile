.PHONY: venv deploy destroy synth train pipeline test
deploy:  ; cd infra && cdk deploy --require-approval never
destroy: ; cd infra && cdk destroy -f
synth:   ; cd infra && cdk synth
pipeline:
	python src/pipeline/run_pipeline.py --bucket $(DATA_BUCKET) --role-arn $(SAGEMAKER_ROLE_ARN)
test:    ; python -m pytest -q
