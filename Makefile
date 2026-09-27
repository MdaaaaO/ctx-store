PYTHON ?= python3

.PHONY: test golden ci

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

# Rewrite tests/golden/ from the current output; review the diff before committing.
golden:
	CTX_UPDATE_GOLDEN=1 $(PYTHON) -m unittest discover -s tests -t .

ci: test
	$(PYTHON) -m compileall -q ctxstore ctx
