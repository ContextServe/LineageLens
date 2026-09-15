"""Contract key normalisation (issue #51 §8.3).

Two sides of a cross-framework hop join only if they compute the same key. They
almost never write it the same way::

    /api/users/{id}        FastAPI, Spring
    /api/users/:id         Express, NestJS
    `/api/users/${id}`     TypeScript template literal
    /api/users/<int:id>    Flask
    /api/users/%s          format string

All five denote one route and must normalise to ``/api/users/{*}``. Getting this
wrong is silent: the contract node splits in two, the ``EXPOSES`` side lands on
one and the ``CONSUMES`` side on the other, and the cross-service link simply
does not appear. So this is its own module with its own tests rather than a
regex inline in an adapter.

What cannot be normalised is reported, not guessed. A path assembled by runtime
concatenation has no static key; :func:`normalise_http` returns a partial with
``dynamic=True`` and the caller records a ``dynamic_contract_key`` boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The single placeholder every path-parameter syntax collapses to. Chosen to be
#: something no real path segment contains, so it cannot collide with a literal.
PLACEHOLDER = "{*}"

_METHODS = frozenset({
    "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE",
    "WEBSOCKET", "ANY",
})

#: Path-parameter spellings, in one alternation so a segment matching any of
#: them becomes the placeholder.
#:
#:   {id}  {id:int}  {*rest}     brace forms (FastAPI, Spring, ASP.NET, Axum)
#:   :id   :id?                  colon form (Express, NestJS)
#:   <id>  <int:id>              angle form (Flask)
#:   ${id} ${obj.id}             template interpolation (JS/TS)
#:   %s %d {}                    format placeholders (Python, Go, Rust)
#:   *     **                    wildcards
_PARAM_SEGMENT = re.compile(
    r"""^(?:
        \{[^}]*\}                # {id}, {id:int}, {*rest}, {}
      | :[A-Za-z_][\w]*\??       # :id, :id?
      | <[^>]*>                  # <id>, <int:id>
      | \$\{[^}]*\}              # ${id}
      | %[sdvq]                  # %s %d %v %q
      | \*{1,2}                  # * or **
    )$""",
    re.VERBOSE,
)

#: An interpolation *inside* a larger segment: `user-${id}.json`. Replaced in
#: place rather than collapsing the whole segment, so the literal parts survive.
_INLINE_PARAM = re.compile(r"\$\{[^}]*\}|%[sdvq]|\{\}")

#: A leading scheme+host, stripped so an absolute URL and a relative path join.
#: The host is genuinely not part of the contract: the same route is reached as
#: `/api/x` from a same-origin frontend and `https://svc/api/x` from another
#: service, and they must land on one node.
_ORIGIN = re.compile(r"^[a-zA-Z][\w+.-]*://[^/]+")

#: Runtime concatenation. `"/api/" + path` or `"/api/" .. path` has no static
#: key, so it is a boundary rather than a guess.
_CONCAT = re.compile(r"[+]\s*\w|\w\s*[+]|\.\.|\bformat\b|\bjoin\b|\+\+")


@dataclass(frozen=True, slots=True)
class NormalisedKey:
    """A contract key, plus whether anything had to be given up to get it."""

    key: str
    #: True when part of the key could not be determined statically. The caller
    #: records a ``dynamic_contract_key`` boundary and may still register the
    #: partial key -- a prefix is a useful lead even when the tail is unknown.
    dynamic: bool = False
    #: What was resolvable, when ``dynamic``. Empty when the whole thing was.
    prefix: str = ""

    def __bool__(self) -> bool:
        return bool(self.key)


def normalise_http(raw: str, method: str = "ANY") -> NormalisedKey:
    """Normalise an HTTP route to ``METHOD /path/with/{*}``.

    ``method`` is upper-cased and validated; anything unrecognised becomes
    ``ANY``, which still joins with a concrete method only if the other side is
    also ``ANY``. Being strict here beats matching ``GET`` against a route that
    was actually registered for ``POST``.
    """
    verb = method.strip().upper()
    if verb not in _METHODS:
        verb = "ANY"

    path = _strip_quotes(raw)
    if not path:
        return NormalisedKey(key="", dynamic=True)

    dynamic = bool(_CONCAT.search(path))
    if dynamic:
        # A concatenation is not a path, so normalising the whole expression
        # would produce a key containing source code. Keep only the leading
        # string literal -- `"/api/" + path` yields `/api/`, which is a real
        # prefix a reader can search on, whereas `/"/api/" + path` is noise.
        path = _leading_literal(path)
        if not path:
            return NormalisedKey(key="", dynamic=True, prefix=f"{verb} ?")

    path = _ORIGIN.sub("", path)
    path = path.split("?", 1)[0].split("#", 1)[0]  # query and fragment are not the route

    if not path.startswith("/"):
        path = "/" + path

    segments: list[str] = []
    for segment in path.split("/"):
        if not segment:
            continue
        if _PARAM_SEGMENT.match(segment):
            segments.append(PLACEHOLDER)
        else:
            replaced = _INLINE_PARAM.sub(PLACEHOLDER, segment)
            segments.append(replaced)

    # A trailing slash is not a distinct route; every framework treats
    # `/users` and `/users/` as one, so keeping both would split the node.
    normalised = "/" + "/".join(segments)
    key = f"{verb} {normalised}"
    prefix = ""
    if dynamic:
        # Everything up to the first placeholder is still known, and a prefix
        # narrows a manual search even when the tail is unknowable.
        head: list[str] = []
        for segment in segments:
            if PLACEHOLDER in segment:
                break
            head.append(segment)
        prefix = f"{verb} /" + "/".join(head)
        # Mark the key itself as partial, so a dynamic route cannot silently
        # join with a static one that happens to share its prefix.
        key = f"{key.rstrip('/')}/{PLACEHOLDER}"

    return NormalisedKey(key=key, dynamic=dynamic, prefix=prefix)


def normalise_fqn(raw: str) -> NormalisedKey:
    """Normalise a fully-qualified type or service name.

    Used for RPC interfaces (Dubbo, gRPC) and SPI keys. Generic parameters are
    dropped: ``Handler<String>`` and ``Handler`` are one service contract, and
    the provider side rarely writes the parameter.
    """
    text = _strip_quotes(raw)
    text = re.sub(r"<[^>]*>", "", text)          # generics
    text = text.replace("::", ".").replace("/", ".")
    text = re.sub(r"\.(class|getName\(\))$", "", text)  # Java `Foo.class`
    return NormalisedKey(key=text.strip(". "))


def normalise_topic(raw: str) -> NormalisedKey:
    """Normalise a queue or topic name.

    Wildcards are preserved rather than collapsed: an AMQP binding of
    ``orders.*`` is a genuinely different subscription from ``orders.created``,
    and conflating them would claim a link that does not exist.
    """
    text = _strip_quotes(raw)
    dynamic = bool(_CONCAT.search(text))
    text = _INLINE_PARAM.sub(PLACEHOLDER, text)
    return NormalisedKey(key=text.strip(), dynamic=dynamic)


def normalise_table(raw: str) -> NormalisedKey:
    """Normalise a database table name: unquoted, lower-cased, schema kept.

    Case folding is correct for the engines that matter here -- Postgres folds
    unquoted identifiers, MySQL is platform-dependent -- and an ORM model name
    and a raw SQL string must agree.
    """
    text = _strip_quotes(raw).strip().strip("`[]\"")
    return NormalisedKey(key=text.lower())


def normalise_plain(raw: str) -> NormalisedKey:
    """Env vars, feature flags, CLI commands: strip quotes, keep the rest.

    Deliberately case-sensitive. ``PATH`` and ``Path`` are different env vars,
    and a flag key is whatever the flag service was given.
    """
    return NormalisedKey(key=_strip_quotes(raw).strip())


#: Dispatch by contract kind. A kind with no entry falls back to
#: :func:`normalise_plain`, which is lossless -- so an adapter for a scheme
#: nobody has written a normaliser for still works, it just joins only on exact
#: text.
NORMALISERS = {
    "http_route": None,   # needs a method; handled by normalise_http directly
    "rpc_service": normalise_fqn,
    "spi": normalise_fqn,
    "graphql": normalise_fqn,
    "topic": normalise_topic,
    "table": normalise_table,
    "env": normalise_plain,
    "flag": normalise_plain,
    "cli": normalise_plain,
}


def normalise(kind: str, raw: str, method: str = "ANY") -> NormalisedKey:
    """Normalise ``raw`` for ``kind``."""
    if kind == "http_route":
        return normalise_http(raw, method)
    fn = NORMALISERS.get(kind) or normalise_plain
    return fn(raw)


_LEADING_LITERAL = re.compile(r"""^\s*["'`]([^"'`]*)["'`]""")


def _leading_literal(text: str) -> str:
    """The first quoted string in a concatenation expression.

    ``"/api/" + path`` -> ``/api/``. Returns ``""`` when the expression opens
    with a variable, in which case nothing about the path is known statically.
    """
    match = _LEADING_LITERAL.match(text)
    return match.group(1) if match else ""


def _strip_quotes(text: str) -> str:
    """Remove the quoting a literal arrives wrapped in.

    Extractors capture the argument node verbatim, so a route arrives as
    ``"/api/users"`` including the quote characters, and template literals
    arrive with backticks. Java text blocks and Python triple quotes appear in
    annotation values often enough to be worth handling.
    """
    value = text.strip()
    for triple in ('"""', "'''"):
        if value.startswith(triple) and value.endswith(triple) and len(value) >= 6:
            return value[3:-3].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'`":
        return value[1:-1].strip()
    return value
