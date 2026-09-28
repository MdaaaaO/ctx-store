#!/usr/bin/env python3
"""Latency of every read verb on a large generated store.

    python3 bench/bench.py [--docs 3000] [--runs 20] [--keep <dir>]

Builds a Markdown store of `--docs` docs (sizes like a real one: most docs a
few KB, some over 30 KB, 17 KB on average), then times each read verb as a
caller sees it: a fresh `ctx` process per call, interpreter start included.
Prints a table for the README. Deterministic: the same arguments build the
same store.
"""
import argparse
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CTX = os.path.join(ROOT, "ctx")
WORDS = ("store lock audit session epic region rollout schema index budget hook reader writer merge "
         "branch review deploy cache token ledger section heading frontmatter migrate archive").split()
TYPES = {
    "epic": {"sections": ["Goal", "Session log"], "log": {"section": "Session log"},
             "frontmatter": {"title": {"required": True}, "updated": {"kind": "date"}}},
    "reference": {"frontmatter": {"title": {"required": True}}},
    "session": {"paths": ["sessions/*.md"], "owner": "session"},
}


def paragraph(rng, size):
    out, total = [], 0
    while total < size:
        line = " ".join(rng.choice(WORDS) for _ in range(rng.randint(8, 16))).capitalize() + "."
        out.append(line)
        total += len(line) + 1
    return "\n".join(out)


def build(root, docs, seed=7):
    rng = random.Random(seed)
    os.makedirs(os.path.join(root, ".ctx", "types"))
    with open(os.path.join(root, "ctx-store.json"), "w") as handle:
        json.dump({"schema_version": 1, "resolve": {"key_regex": "EX-[0-9]+", "fields": ["epic"],
                                                    "section": "Tracker & links"}}, handle)
    for name, schema in TYPES.items():
        with open(os.path.join(root, ".ctx", "types", name + ".json"), "w") as handle:
            json.dump(schema, handle)
    total = 0
    for number in range(docs):
        kind = ("epic", "reference", "reference", "session")[number % 4]
        size = rng.choice((2000, 4000, 8000, 12000, 20000, 60000))
        if kind == "session":
            key = f"sessions/s{number:05}"
            text = (f"---\nsession: s{number:05}\nsession_id: sid-{number}\nstatus: {'ended' if number % 8 else 'active'}\n"
                    f"epic: EX-{number}\nworking_on: {paragraph(rng, 40)}\nheartbeat: 2026-01-07T10:00:00Z\n---\n\n"
                    f"# Session\n\n## Notes\n{paragraph(rng, 1500)}\n")
        elif kind == "epic":
            key = f"epics/area{number % 40:02}/e{number:05}"
            log = "\n".join(f"- 2026-01-{day % 28 + 1:02} — {paragraph(rng, 60)}" for day in range(size // 400))
            text = (f"---\ntitle: Epic {number}\ntype: epic\nstatus: active\ntags: [t{number % 50}, bench]\n"
                    f"updated: 2026-01-{number % 28 + 1:02}\n---\n\n# Epic {number}\n\n## Tracker & links\n- EX-{number}\n\n"
                    f"## Goal\n{paragraph(rng, size // 2)}\n\n## Session log\n{log}\n")
        else:
            key = f"reference/area{number % 40:02}/r{number:05}"
            text = (f"---\ntitle: Reference {number}\ntype: reference\ntags: [t{number % 50}]\n"
                    f"updated: 2026-01-{number % 28 + 1:02}\n---\n\n# Reference {number}\n\n## Summary\n"
                    f"{paragraph(rng, size)}\n")
        if number == docs - 1:
            text += "\nThe only doc that names the quokka.\n"
        path = os.path.join(root, key + ".md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        total += len(text.encode("utf-8"))
    return total


def timed(args, env, runs):
    took = []
    for _ in range(runs):
        start = time.perf_counter()
        done = subprocess.run([sys.executable, CTX, *args], env=env, capture_output=True)
        took.append((time.perf_counter() - start) * 1000)
        if done.returncode != 0:
            sys.exit(f"ctx {' '.join(args)} failed: {done.stderr.decode()[:400]}")
    took.sort()
    return statistics.median(took), took[max(0, int(len(took) * 0.95) - 1)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", type=int, default=3000)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--keep")
    options = parser.parse_args()
    work = options.keep or tempfile.mkdtemp(prefix="ctx-bench-")
    store = os.path.join(work, "store")
    if not os.path.isdir(store):
        size = build(store, options.docs)
    else:
        size = sum(os.path.getsize(os.path.join(f, n)) for f, _, names in os.walk(store) for n in names if n.endswith(".md"))
    env = {"CTX_STORE": store, "CTX_NO_WALK": "1", "CTX_ACTOR": "bench"}
    adopt = subprocess.run([sys.executable, CTX, "validate", "--changed", "--adopt"], env=env, capture_output=True)
    if adopt.returncode != 0:
        sys.exit("setup failed: " + adopt.stderr.decode()[:400])
    last = options.docs - 1
    epic = f"epics/area{(last - last % 4) % 40:02}/e{last - last % 4:05}"
    calls = (
        ("start of the interpreter (`--version`)", ("--version",)),
        ("`brief <doc>`", ("brief", epic)),
        ("`get <doc> --section --tail 5`", ("get", epic, "--section", "Session log", "--tail", "5")),
        ("`view <doc>`", ("view", epic)),
        ("`brief --registry`", ("brief", "--registry")),
        ("`brief --session <id>`", ("brief", "--session", f"sid-{last - last % 4 + 3 if last % 4 == 3 else 3}")),
        ("`resolve <key>`", ("resolve", "EX-3")),
        ("`find <word in one doc>`", ("find", "quokka")),
        ("`find <common word>`", ("find", "rollout")),
        ("`find --tag`", ("find", "--tag", "t7")),
        ("`validate`", ("validate",)),
        ("`validate --changed`", ("validate", "--changed")),
    )
    print(f"{options.docs} docs, {size / 1e6:.1f} MB, {options.runs} runs per call, Python {sys.version.split()[0]}\n")
    print("| Call | p50 ms | p95 ms |\n|---|---:|---:|")
    for label, args in calls:
        p50, p95 = timed(args, env, options.runs)
        print(f"| {label} | {p50:.0f} | {p95:.0f} |")
    if not options.keep:
        shutil.rmtree(work)


if __name__ == "__main__":
    main()
