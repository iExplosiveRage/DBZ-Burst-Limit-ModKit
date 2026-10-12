"""TOML reading for slot.toml / mod.toml / mods.toml.

Python 3.11+ has tomllib; older Pythons get this small parser for the subset these files use:
comments, [table], [[array.of.tables]], dotted table names, bare / quoted keys,
"basic" and 'literal' strings, integers (decimal, 0x hex, underscores), floats, booleans,
(multi-line, nested) arrays and inline tables.
"""
try:
    import tomllib as _tomllib
except ImportError:  # Python < 3.11
    _tomllib = None


class TomlError(ValueError):
    pass


def loads(text):
    if _tomllib is not None:
        try:
            return _tomllib.loads(text)
        except _tomllib.TOMLDecodeError as e:
            raise TomlError(str(e))
    return _Parser(text).parse()


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return loads(fh.read())


_ESC = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "b": "\b", "f": "\f"}


class _Parser:
    def __init__(self, text):
        self.s = text.replace("\r\n", "\n")
        self.i = 0
        self.line = 1

    def err(self, msg):
        raise TomlError("line %d: %s" % (self.s.count("\n", 0, self.i) + 1, msg))

    def peek(self):
        return self.s[self.i] if self.i < len(self.s) else ""

    def skip_ws(self, newlines=False):
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in " \t" or (newlines and c == "\n"):
                self.i += 1
            elif c == "#":
                while self.i < len(self.s) and self.s[self.i] != "\n":
                    self.i += 1
            else:
                break

    def parse(self):
        root = {}
        cur = root
        while True:
            self.skip_ws(True)
            if self.i >= len(self.s):
                return root
            if self.s.startswith("[[", self.i):
                self.i += 2
                keys = self.key_path("]]")
                self.i += 2
                parent = self.descend(root, keys[:-1])
                arr = parent.setdefault(keys[-1], [])
                if not isinstance(arr, list):
                    self.err("%s is not an array of tables" % keys[-1])
                cur = {}
                arr.append(cur)
            elif self.peek() == "[":
                self.i += 1
                keys = self.key_path("]")
                self.i += 1
                cur = self.descend(root, keys)
            else:
                keys = self.key_path("=")
                self.i += 1
                self.skip_ws()
                val = self.value()
                tgt = self.descend(cur, keys[:-1])
                if keys[-1] in tgt:
                    self.err("duplicate key %s" % keys[-1])
                tgt[keys[-1]] = val
            self.skip_ws()
            if self.i < len(self.s) and self.s[self.i] != "\n":
                self.err("unexpected text %r" % self.s[self.i:self.i + 20])

    def descend(self, table, keys):
        for k in keys:
            nxt = table.setdefault(k, {})
            if isinstance(nxt, list):
                nxt = nxt[-1]
            if not isinstance(nxt, dict):
                self.err("%s is not a table" % k)
            table = nxt
        return table

    def key_path(self, end):
        keys = []
        while True:
            self.skip_ws()
            c = self.peek()
            if c == '"':
                keys.append(self.basic_string())
            elif c == "'":
                keys.append(self.literal_string())
            else:
                j = self.i
                while self.i < len(self.s) and (self.s[self.i].isalnum() or self.s[self.i] in "_-"):
                    self.i += 1
                if j == self.i:
                    self.err("expected a key")
                keys.append(self.s[j:self.i])
            self.skip_ws()
            if self.peek() == ".":
                self.i += 1
                continue
            if not self.s.startswith(end, self.i):
                self.err("expected %r" % end)
            return keys

    def basic_string(self):
        if self.s.startswith('"""', self.i):
            self.i += 3
            if self.peek() == "\n":
                self.i += 1
            out = []
            while not self.s.startswith('"""', self.i):
                if self.i >= len(self.s):
                    self.err("unterminated string")
                out.append(self.char_or_escape())
            self.i += 3
            return "".join(out)
        self.i += 1
        out = []
        while self.peek() != '"':
            if self.peek() in ("", "\n"):
                self.err("unterminated string")
            out.append(self.char_or_escape())
        self.i += 1
        return "".join(out)

    def char_or_escape(self):
        c = self.s[self.i]
        self.i += 1
        if c != "\\":
            return c
        e = self.s[self.i]
        self.i += 1
        if e in _ESC:
            return _ESC[e]
        if e in "uU":
            n = 4 if e == "u" else 8
            v = self.s[self.i:self.i + n]
            self.i += n
            return chr(int(v, 16))
        self.err("bad escape \\%s" % e)

    def literal_string(self):
        self.i += 1
        j = self.s.find("'", self.i)
        if j < 0:
            self.err("unterminated string")
        v = self.s[self.i:j]
        self.i = j + 1
        return v

    def value(self):
        c = self.peek()
        if c == '"':
            return self.basic_string()
        if c == "'":
            return self.literal_string()
        if c == "[":
            self.i += 1
            arr = []
            while True:
                self.skip_ws(True)
                if self.peek() == "]":
                    self.i += 1
                    return arr
                arr.append(self.value())
                self.skip_ws(True)
                if self.peek() == ",":
                    self.i += 1
                elif self.peek() != "]":
                    self.err("expected , or ] in an array")
        if c == "{":
            self.i += 1
            tbl = {}
            while True:
                self.skip_ws()
                if self.peek() == "}":
                    self.i += 1
                    return tbl
                keys = self.key_path("=")
                self.i += 1
                self.skip_ws()
                self.descend(tbl, keys[:-1])[keys[-1]] = self.value()
                self.skip_ws()
                if self.peek() == ",":
                    self.i += 1
        j = self.i
        while self.i < len(self.s) and self.s[self.i] not in ",]}\n#" and self.s[self.i] not in " \t":
            self.i += 1
        tok = self.s[j:self.i]
        if tok == "true":
            return True
        if tok == "false":
            return False
        t = tok.replace("_", "")
        try:
            if t.lower().startswith(("0x", "+0x", "-0x")):
                return int(t, 16)
            if t.lower().startswith("0o"):
                return int(t[2:], 8)
            if t.lower().startswith("0b"):
                return int(t[2:], 2)
            if any(ch in t for ch in ".eE") and not t.lower().startswith("0x"):
                return float(t)
            return int(t)
        except ValueError:
            self.err("can't read the value %r" % tok)


def quote(s):
    """A TOML basic string."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'
