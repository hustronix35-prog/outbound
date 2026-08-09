.PHONY: setup init run daemon admin status test

setup:
	python -m venv .venv
	.venv/Scripts/pip install -e ".[dev]" || .venv/bin/pip install -e ".[dev]"
	cp -n .env.example .env || copy .env.example .env

init:
	gtm init

run:
	gtm run

daemon:
	gtm daemon

admin:
	gtm admin

status:
	gtm status

test:
	pytest -q
