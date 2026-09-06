"""Parser for the minified JS object literal Nuxt embeds in `window.__NUXT__`.

The payload has the shape

    (function(a,b,c,...){return {...literal...}}("",0,null,...))

where every repeated value in the literal is replaced by a single-letter
parameter name. Parsing it (instead of running it through a JS engine) keeps
the scraper dependency-free and avoids executing code fetched from the network.
"""

import re

__all__ = ["JsParseError", "parse_nuxt_payload", "parse_js_value"]

_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUMBER = re.compile(r"-?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_WS = re.compile(r"\s+")

_STRING_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v",
    "0": "\0", "\\": "\\", "'": "'", '"': '"', "/": "/", "\n": "",
}


class JsParseError(ValueError):
    """Raised when the payload does not look like the structure we expect."""


class _Parser:
    def __init__(self, text, pos=0):
        self.text = text
        self.pos = pos

    # -- helpers ---------------------------------------------------------
    def error(self, message):
        snippet = self.text[max(0, self.pos - 40):self.pos + 40]
        return JsParseError("%s at offset %d near: %s" % (message, self.pos, snippet))

    def skip_ws(self):
        match = _WS.match(self.text, self.pos)
        if match:
            self.pos = match.end()

    def peek(self):
        self.skip_ws()
        return self.text[self.pos:self.pos + 1]

    def expect(self, char):
        if self.peek() != char:
            raise self.error("expected %r" % char)
        self.pos += 1

    # -- values ----------------------------------------------------------
    def parse_value(self):
        char = self.peek()
        if char == "":
            raise self.error("unexpected end of payload")
        if char in "\"'":
            return self.parse_string()
        if char == "{":
            return self.parse_object()
        if char == "[":
            return self.parse_array()
        if char == "-" or char.isdigit() or (char == "." and self.text[self.pos + 1:self.pos + 2].isdigit()):
            return self.parse_number()
        match = _IDENT.match(self.text, self.pos)
        if not match:
            raise self.error("unexpected character %r" % char)
        word = match.group()
        self.pos = match.end()
        if word == "true":
            return True
        if word == "false":
            return False
        if word == "null":
            return None
        if word == "undefined":
            return None
        if word == "void":
            self.parse_value()  # `void 0` -> undefined
            return None
        return _Ref(word)

    def parse_string(self):
        quote = self.text[self.pos]
        self.pos += 1
        out = []
        text = self.text
        while True:
            char = text[self.pos:self.pos + 1]
            if char == "":
                raise self.error("unterminated string")
            self.pos += 1
            if char == quote:
                return "".join(out)
            if char != "\\":
                out.append(char)
                continue
            esc = text[self.pos:self.pos + 1]
            self.pos += 1
            if esc == "u":
                if text[self.pos:self.pos + 1] == "{":
                    end = text.index("}", self.pos)
                    out.append(chr(int(text[self.pos + 1:end], 16)))
                    self.pos = end + 1
                else:
                    out.append(chr(int(text[self.pos:self.pos + 4], 16)))
                    self.pos += 4
            elif esc == "x":
                out.append(chr(int(text[self.pos:self.pos + 2], 16)))
                self.pos += 2
            else:
                out.append(_STRING_ESCAPES.get(esc, esc))

    def parse_number(self):
        match = _NUMBER.match(self.text, self.pos)
        if not match:
            raise self.error("malformed number")
        self.pos = match.end()
        raw = match.group()
        if raw[:2].lower() == "0x" or raw[:3].lower() in ("-0x",):
            return int(raw, 16)
        if "." in raw or "e" in raw or "E" in raw:
            return float(raw)
        return int(raw)

    def parse_array(self):
        self.expect("[")
        items = []
        if self.peek() == "]":
            self.pos += 1
            return items
        while True:
            if self.peek() == ",":       # elision: [1,,2]
                self.pos += 1
                items.append(None)
                continue
            items.append(self.parse_value())
            char = self.peek()
            if char == ",":
                self.pos += 1
                if self.peek() == "]":   # trailing comma
                    self.pos += 1
                    return items
                continue
            if char == "]":
                self.pos += 1
                return items
            raise self.error("expected ',' or ']'")

    def parse_object(self):
        self.expect("{")
        obj = {}
        if self.peek() == "}":
            self.pos += 1
            return obj
        while True:
            char = self.peek()
            if char in "\"'":
                key = self.parse_string()
            else:
                match = _IDENT.match(self.text, self.pos) or _NUMBER.match(self.text, self.pos)
                if not match:
                    raise self.error("expected object key")
                key = match.group()
                self.pos = match.end()
            self.expect(":")
            obj[key] = self.parse_value()
            char = self.peek()
            if char == ",":
                self.pos += 1
                if self.peek() == "}":   # trailing comma
                    self.pos += 1
                    return obj
                continue
            if char == "}":
                self.pos += 1
                return obj
            raise self.error("expected ',' or '}'")


class _Ref(object):
    """A reference to one of the wrapper function's parameters."""

    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name


def _resolve(value, scope, seen=None):
    if isinstance(value, _Ref):
        if value.name not in scope:
            raise JsParseError("unknown identifier %r in payload" % value.name)
        seen = seen or set()
        if value.name in seen:
            raise JsParseError("circular reference to %r in payload" % value.name)
        seen.add(value.name)
        return _resolve(scope[value.name], scope, seen)
    if isinstance(value, dict):
        return dict((k, _resolve(v, scope)) for k, v in value.items())
    if isinstance(value, list):
        return [_resolve(v, scope) for v in value]
    return value


def parse_js_value(text, pos=0):
    """Parse a single JS literal, returning ``(value, end_offset)``."""
    parser = _Parser(text, pos)
    value = parser.parse_value()
    return value, parser.pos


def parse_nuxt_payload(payload):
    """Turn a ``window.__NUXT__`` assignment body into plain Python data."""
    match = re.match(r"\s*\(?\s*function\s*\(([^)]*)\)\s*\{\s*return\s*", payload)
    if not match:
        # Some builds inline the state without the wrapper function.
        value, _ = parse_js_value(payload)
        return _resolve(value, {})

    params = [p.strip() for p in match.group(1).split(",") if p.strip()]
    body, pos = parse_js_value(payload, match.end())

    parser = _Parser(payload, pos)
    parser.expect("}")
    parser.expect("(")
    args = []
    if parser.peek() != ")":
        while True:
            args.append(parser.parse_value())
            char = parser.peek()
            if char == ",":
                parser.pos += 1
                continue
            break
    parser.expect(")")

    scope = {}
    for index, name in enumerate(params):
        scope[name] = args[index] if index < len(args) else None
    return _resolve(body, scope)
