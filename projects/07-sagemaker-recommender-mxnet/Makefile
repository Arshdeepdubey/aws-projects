.PHONY: deploy destroy synth prepare train load test
deploy:  ; cd infra && cdk deploy --require-approval never
destroy: ; cd infra && cdk destroy -f
synth:   ; cd infra && cdk synth
prepare: ; python src/data/prepare.py --ratings $(RATINGS) --out ./data --implicit
train:   ; python src/training/launch.py --bucket $(DATA_BUCKET) --role-arn $(ROLE_ARN)
load:    ; python src/serving/load_recommendations.py --bucket $(DATA_BUCKET) --table $(TABLE)
test:    ; python -m pytest -q
