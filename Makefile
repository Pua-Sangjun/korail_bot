VENV := .venv
PYTHON := $(VENV)/bin/python

help:           ## Show this help.
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//'

$(PYTHON):
	python3 -m venv $(VENV)

.PHONY: install
install: $(PYTHON)	## Create .venv and install dependencies
	$(PYTHON) -m pip install -r requirements-dev.txt

.PHONY: run
run:			## Start the reservation search in the terminal
	$(PYTHON) src/cli.py

.PHONY: test
test:			## Run tests
	$(PYTHON) -m pytest tests/ -v
