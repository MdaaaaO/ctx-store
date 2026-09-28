#!/usr/bin/env python3
"""What `find` finds: a fixed set of lookups against a fixed store.

    python3 bench/eval.py [--json]

Each lookup in `bench/eval/queries.json` names the doc (and section) that is
the right answer. The lookups are shaped like the ones a consumer's skills
run today; `from` is the number of the lookup they were modelled on (#21).
For every lookup this prints where the right doc came in `find`'s result:
its rank, or `miss`. The section is not part of `find`'s answer yet, so it
is recorded, not scored.
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ctxstore import cli  # noqa: E402

STORE = os.path.join(ROOT, "bench", "eval", "store")
QUERIES = os.path.join(ROOT, "bench", "eval", "queries.json")


def rank(query, doc):
    out, err = io.StringIO(), io.StringIO()
    environ = {"CTX_STORE": STORE, "CTX_NO_WALK": "1", "CTX_ACTOR": "eval"}
    code = cli.main(["find", "--budget", "8192", "--", query], environ, out, err)
    if code != 0:
        sys.exit(f"find failed for {query!r}: {err.getvalue()}")
    keys = [line.split(" · ")[0] for line in out.getvalue().splitlines()[1:]]
    return keys.index(doc) + 1 if doc in keys else 0, len(keys)


def main():
    with open(QUERIES, encoding="utf-8") as handle:
        queries = json.load(handle)
    results = []
    for query in queries:
        place, hits = rank(query["query"], query["doc"])
        results.append({**query, "rank": place, "hits": hits})
    if "--json" in sys.argv:
        print(json.dumps(results, indent=2))
        return
    print("| Lookup | Kind | Query | Hits | Right doc |\n|---|---|---|---:|---|")
    for r in results:
        print(f"| {r['id']} | {r['kind']} | `{r['query']}` | {r['hits']} | {'rank ' + str(r['rank']) if r['rank'] else 'miss'} |")
    print()
    kinds = []
    for r in results:
        if r["kind"] not in kinds:
            kinds.append(r["kind"])
    for kind in kinds + ["all"]:
        chosen = [r for r in results if kind in (r["kind"], "all")]
        first = sum(1 for r in chosen if r["rank"] == 1)
        found = sum(1 for r in chosen if r["rank"])
        print(f"{kind}: {first} of {len(chosen)} at rank 1, {found} of {len(chosen)} found")


if __name__ == "__main__":
    main()
