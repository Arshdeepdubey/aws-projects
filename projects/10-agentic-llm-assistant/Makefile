.PHONY: deploy destroy synth seed chat test logs
deploy:  ; cd infra && cdk deploy --require-approval never
destroy: ; cd infra && cdk destroy -f
synth:   ; cd infra && cdk synth
seed:    ; python src/seed/seed_data.py --orders-table $(ORDERS_TABLE)
chat:    ; python scripts/chat.py --api $(API_URL)
test:    ; python -m pytest -q
logs:    ; aws logs tail /aws/lambda/agentic-assistant-dev-chat --follow --format short
