"""Command line front-end. No prompts, no colour, deterministic output."""
import os
import sys

from . import __version__, doctor, fs, spec
from .config import Config
from .contract import API, CtxError, dump, ok_envelope

# Every verb of the interface; a verb without a handler is specified but not
# built yet.
VERBS = (
    "view", "create", "str_replace", "insert", "delete", "rename",
    "log", "fm", "row", "new", "move",
    "brief", "find", "resolve", "get",
    "validate", "doctor", "maintain", "touch", "migrate",
)


def _parse(argv):
    """Split global options from the verb and its arguments. Unknown options
    are rejected."""
    options = {"json": False, "store": None, "version": False}
    rest = []
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--json":
            options["json"] = True
        elif arg == "--version":
            options["version"] = True
        elif arg == "--store":
            if not args:
                raise CtxError("USAGE", "--store")
            options["store"] = args.pop(0)
        elif arg.startswith("--store="):
            options["store"] = arg.split("=", 1)[1]
        elif arg in ("-h", "--help"):
            rest.insert(0, "help")
        elif arg.startswith("-") and arg != "-":
            raise CtxError("USAGE", arg)
        else:
            rest.append(arg)
    return options, rest


def _help(args):
    if len(args) > 1:
        raise CtxError("USAGE", "help")
    if not args:
        return {"topic": "", "text": spec.overview()}
    name = args[0]
    verbs = spec.verbs()
    if name in verbs:
        return {"topic": name, "text": f"ctx {name}\n\n{verbs[name]}"}
    topics = spec.topics()
    if name in topics:
        heading, body = topics[name]
        return {"topic": name, "text": f"{heading}\n\n{body}"}
    raise CtxError("USAGE", name)


def _doctor(args, options, environ):
    if args:
        raise CtxError("USAGE", args[0])
    config = Config(environ, options["store"])
    roots = fs.resolve_stores(config.stores, fs.cwd(), config.walk)
    return doctor.report(config, roots)


def run(argv, environ, out):
    """Returns the text to print on success; raises CtxError otherwise."""
    options, rest = _parse(argv)
    if options["version"]:
        verb, data = "version", {"version": __version__}
        text = f"ctx {__version__} (api {API})"
    elif not rest or rest[0] == "help":
        verb, data = "help", _help(rest[1:])
        text = data["text"]
    elif rest[0] == "doctor":
        verb, data = "doctor", _doctor(rest[1:], options, environ)
        text = doctor.text(data)
    elif rest[0] in VERBS:
        raise CtxError("USAGE", f"{rest[0]} is not available in ctx {__version__}")
    else:
        raise CtxError("USAGE", rest[0])
    out.write((dump(ok_envelope(verb, data)) if options["json"] else text) + "\n")


def main(argv=None, environ=None, out=None, err=None):
    argv = sys.argv[1:] if argv is None else argv
    environ = os.environ if environ is None else environ
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    try:
        run(argv, environ, out)
    except CtxError as failure:
        if "--json" in argv:
            out.write(dump(failure.envelope()) + "\n")
        err.write(failure.line() + "\n")
        return failure.exit_code
    return 0
