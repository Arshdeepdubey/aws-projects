.PHONY: build deploy guided invoke test lint delete
STACK ?= image-processing

build:   ; sam build --use-container
deploy: build ; sam deploy --stack-name $(STACK)
guided: build ; sam deploy --guided --stack-name $(STACK)
invoke: build ; sam local invoke ProcessorFunction -e events/sqs-s3-put.json
test:    ; python -m pytest -q
lint:    ; sam validate --lint
delete:  ; sam delete --stack-name $(STACK)
