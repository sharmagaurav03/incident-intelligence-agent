.PHONY: test lint run serve docker

test:
	python3 -m pytest -q

lint:
	python3 -m pyflakes src/triage_agent tests

run:
	PYTHONPATH=src python3 -m triage_agent run --config config/default.yaml

serve:
	PYTHONPATH=src python3 -m triage_agent serve --config config/default.yaml

docker:
	docker build -t triage-agent .
