.PHONY: deploy destroy synth index ingest ask test
deploy:  ; cd infra && cdk deploy --require-approval never
destroy: ; cd infra && cdk destroy -f
synth:   ; cd infra && cdk synth
index:   ; python scripts/create_index.py --endpoint $(AOSS_ENDPOINT) --index multimodal-rag
ingest:  ; python scripts/ingest_local.py --bucket $(DOCS_BUCKET) --path $(PATH_TO_DOCS)
ask:     ; python scripts/ask.py --api $(API_URL) --question "$(Q)"
test:    ; python -m pytest -q
