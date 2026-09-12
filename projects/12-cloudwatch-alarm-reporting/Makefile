.PHONY: build deploy guided test lint report delete
STACK ?= alarm-reporting

build:  ; sam build
deploy: build ; sam deploy --stack-name $(STACK)
guided: build ; sam deploy --guided --stack-name $(STACK)
test:   ; python -m pytest -q
lint:   ; sam validate --lint
report:
	aws lambda invoke --function-name $(STACK)-dev-reporter \
	  --payload '{"windowHours":24}' --cli-binary-format raw-in-base64-out /dev/stdout
delete: ; sam delete --stack-name $(STACK)
