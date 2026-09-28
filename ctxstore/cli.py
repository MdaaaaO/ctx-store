"""Command line front-end. No prompts, no colour, deterministic output."""
import json
import os
import sys

from . import __version__, doctor, fs, mcp, memory_tool, reads, spec, verbs, writes
from .config import Config
from .contract import API, CtxError, dump, ok_envelope
from .store import Store

# Every verb of the interface; a verb that is not in BUILT is specified but
# not built yet.
VERBS = (
    "view", "create", "str_replace", "insert", "delete", "rename",
    "log", "fm", "row", "new", "move",
    "brief", "find", "resolve", "get",
    "validate", "doctor", "maintain", "touch", "migrate",
)

# verb -> (positional parameters, options: name -> kind)
BUILT = {
    "validate": ((), {"changed": "flag", "adopt": "flag"}),
    "log": (("doc", "text"), {"section": "text", "date": "text", "from": "file"}),
    "fm": (("doc", "field", "value"), {"from": "file"}),
    "touch": ((), {"session": "text", "working": "text"}),
    "brief": (("doc",), {"registry": "flag", "session": "text", "budget": "int", "full": "flag"}),
    "get": (("doc",), {"section": "text", "tail": "int", "budget": "int", "full": "flag", "out": "text"}),
    "find": (("query",), {"type": "text", "tag": "text", "budget": "int", "full": "flag", "out": "text"}),
    "resolve": (("key",), {}),
    "view": (("doc",), {"range": "text", "budget": "int", "full": "flag"}),
    "create": (("doc", "text"), {"type": "text", "title": "text", "from": "file"}),
    "new": (("type", "doc"), {"title": "text"}),
    "str_replace": (("doc",), {"old": "text", "new": "text"}),
    "insert": (("doc", "text"), {"line": "int", "from": "file"}),
    "delete": (("doc",), {}),
    "rename": (("doc", "to"), {}),
    "move": (("doc", "to"), {}),
}
WRITES = ("create", "new", "str_replace", "insert", "delete", "rename", "move")
READS = ("brief", "get", "find", "resolve", "validate")
REQUIRED = {
    "log": ("doc", "text"), "fm": ("doc", "field", "value"), "touch": ("session",),
    "get": ("doc",), "resolve": ("key",),
    "create": ("doc",), "new": ("type", "doc"), "str_replace": ("doc", "old"),
    "insert": ("doc", "text", "line"), "delete": ("doc",), "rename": ("doc", "to"), "move": ("doc", "to"),
}
PAYLOAD = {"log": "text", "fm": "value", "create": "text", "insert": "text"}  # what --from fills


def _globals(argv):
    """Split the global options from the verb and its arguments."""
    options = {"json": False, "store": None, "version": False, "now": None, "stdin": False}
    rest = []
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--":
            rest += ["--", *args]
            break
        name, _, inline = arg.partition("=")
        if arg in ("--json", "--version", "--stdin"):
            options[arg[2:]] = True
        elif name in ("--store", "--now"):
            if not inline and not args:
                raise CtxError("USAGE", name)
            options[name[2:]] = inline or args.pop(0)
        elif arg in ("-h", "--help"):
            rest.insert(0, "help")
        else:
            rest.append(arg)
    return options, rest


def _params(verb, args, stdin):
    """The verb's parameters from the command line, or from one JSON object on
    stdin (`--stdin`). Unknown options and keys are rejected."""
    positional, options = BUILT[verb]
    params = {}
    if stdin is not None:
        try:
            given = json.loads(stdin)
        except ValueError:
            given = None
        if not isinstance(given, dict) or args:
            raise CtxError("USAGE", "--stdin")
        params = typed(verb, given)
    else:
        words, args, literal = [], list(args), False
        while args:
            arg = args.pop(0)
            if not literal and arg == "--":
                literal = True
            elif not literal and arg.startswith("--"):
                name, has, inline = arg[2:].partition("=")
                kind = options.get(name)
                if kind is None or (kind == "flag" and has) or name in params:
                    raise CtxError("USAGE", f"--{name}")
                if kind == "flag":
                    params[name] = True
                    continue
                if not has and not args:
                    raise CtxError("USAGE", f"--{name}")
                value = inline if has else args.pop(0)
                if kind == "int":
                    if not value.isdigit():
                        raise CtxError("USAGE", f"--{name}")
                    value = int(value)
                params[name] = value
            else:
                words.append(arg)
        if len(words) > len(positional):
            raise CtxError("USAGE", words[len(positional)])
        params.update(zip(positional, words))
    if "from" in params:
        target = PAYLOAD[verb]
        if target in params:
            raise CtxError("USAGE", "--from")
        source = params.pop("from")
        data = sys.stdin.buffer.read() if source == "-" and stdin is None else fs.read_payload(source)
        try:
            params[target] = data.decode("utf-8")
        except UnicodeDecodeError:
            raise CtxError("USAGE", "--from") from None
    return required(verb, params)


def typed(verb, given, files=True):
    """Parameters given as a JSON object, checked against the verb's own."""
    positional, options = BUILT[verb]
    params = {}
    for key, value in given.items():
        kind = options.get(key) or ("text" if key in positional else None)
        wrong = (
            kind is None
            or (kind == "file" and not files)
            or (kind == "flag" and not isinstance(value, bool))
            or (kind == "int" and type(value) is not int)
            or (kind in ("text", "file") and not isinstance(value, str))
        )
        if wrong:
            raise CtxError("USAGE", key)
        if not (kind == "flag" and value is False):
            params[key] = value
    return params


def required(verb, params):
    for name in REQUIRED.get(verb, ()):
        if name not in params:
            raise CtxError("USAGE", name)
    return params


def dispatch(verb, params, environ, store=None, now=None):
    """Run one built verb; (data, text). Every front-end ends here."""
    if now:
        params = {**params, "now": now}
    config = Config(environ, store)
    roots = fs.resolve_stores(config.stores, fs.cwd(), config.walk)
    stores = [Store(root, config, named=config.source != "walk") for root in roots]
    if verb in ("get", "find", "resolve"):
        return getattr(reads, verb)(stores, params, config)
    if verb in WRITES or verb == "view":
        return getattr(writes, verb)(_first(stores, verb, params), params)
    return getattr(verbs, verb)(_first(stores, verb, params), params)


def _wants_json(argv):
    """Whether --json was given as an option, for a run that failed before or
    while its command line was parsed. An option's value is not an option."""
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--":
            return False
        if arg == "--json":
            return True
        if arg in ("--store", "--now") and args:
            args.pop(0)
    return False


def _first(stores, verb, params):
    """The store a single-store verb runs on: the first one that holds the doc
    it names, else the first one."""
    if len(stores) > 1 and params.get("doc"):
        for store in stores:
            if store.has(params["doc"]):
                return store
    return stores[0]


def _help(args):
    if len(args) > 1:
        raise CtxError("USAGE", "help")
    if not args:
        return {"topic": "", "text": spec.overview()}
    name = args[0]
    sections = spec.verbs()
    if name in sections:
        return {"topic": name, "text": f"ctx {name}\n\n{sections[name]}"}
    topics = spec.topics()
    if name in topics:
        heading, body = topics[name]
        return {"topic": name, "text": f"{heading}\n\n{body}"}
    raise CtxError("USAGE", name)


def run(argv, environ, out, stdin=None):
    """Prints the result on success; raises CtxError otherwise."""
    options, rest = _globals(argv)
    verb = rest[0] if rest else "help"
    if options["version"]:
        verb, data = "version", {"version": __version__}
        text = f"ctx {__version__} (api {API})"
    elif verb == "help":
        data = _help(rest[1:])
        text = data["text"]
    elif verb == "doctor":
        if rest[1:]:
            raise CtxError("USAGE", rest[1])
        config = Config(environ, options["store"])
        roots = fs.resolve_stores(config.stores, fs.cwd(), config.walk)
        data = doctor.report(config, roots)
        text = doctor.text(data)
    elif verb in BUILT:
        payload = None
        if options["stdin"]:
            payload = (sys.stdin if stdin is None else stdin).read()
        params = _params(verb, rest[1:], payload)
        data, text = dispatch(verb, params, environ, options["store"], options["now"])
    elif verb == "memory":
        if rest[1:]:
            raise CtxError("USAGE", rest[1])
        source = sys.stdin if stdin is None else stdin
        data, text = memory_tool.run(source.read(), environ, options["store"], options["now"])
    elif verb == "mcp":
        if rest[1:]:
            raise CtxError("USAGE", rest[1])
        mcp.serve(sys.stdin if stdin is None else stdin, out, environ, options["store"])
        return
    elif verb in VERBS:
        raise CtxError("NOT_BUILT", verb)
    else:
        raise CtxError("USAGE", verb)
    out.write((dump(ok_envelope(verb, data)) if options["json"] else text) + "\n")


def main(argv=None, environ=None, out=None, err=None):
    argv = sys.argv[1:] if argv is None else argv
    environ = os.environ if environ is None else environ
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    try:
        run(argv, environ, out)
    except CtxError as failure:
        if _wants_json(argv):
            out.write(dump(failure.envelope()) + "\n")
        err.write(failure.line() + "\n")
        return failure.exit_code
    return 0
