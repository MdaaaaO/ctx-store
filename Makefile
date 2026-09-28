PYTHON ?= python3

.PHONY: test golden ci bench

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

# Rewrite tests/golden/ from the current output; review the diff before committing.
golden:
	CTX_UPDATE_GOLDEN=1 $(PYTHON) -m unittest discover -s tests -t .

ci: test
	$(PYTHON) -m compileall -q ctxstore ctx

# Latency of the read verbs on a generated 3 000-doc store; prints the README's table.
bench:
	$(PYTHON) bench/bench.py
