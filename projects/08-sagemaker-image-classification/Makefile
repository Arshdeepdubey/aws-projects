.PHONY: deploy destroy synth manifests train test
deploy:  ; cd infra && cdk deploy --require-approval never
destroy: ; cd infra && cdk destroy -f
synth:   ; cd infra && cdk synth
manifests: ; python src/data/build_manifests.py --bucket $(IMAGES_BUCKET) --prefix raw --out-prefix manifests
train:   ; python src/training/launch.py --bucket $(IMAGES_BUCKET) --role-arn $(ROLE_ARN)
test:    ; python -m pytest -q
