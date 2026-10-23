"""File-manager helpers for the web UI console (Android ↔ local copy).

Pure, testable pieces: shell quoting for adb paths, parsing the device's
`stat` listing, a rooted local listing with realpath containment, and
non-overwriting target names. Transfers are copy-only (adb pull/push) — the
console never deletes or overwrites anything.
"""
from __future__ import annotations
import os


def shq(s: str) -> str:
    """Single-quote a string for the device shell (`'` → `'\\''`)."""
    return "'" + str(s).replace("'", "'\\''") + "'"


_TYPES = {"directory": "dir", "regular file": "file", "regular empty file": "file",
          "symbolic link": "link"}


def parse_stat_lines(out: str) -> list[dict]:
    """`%F|%s|%Y|%n` lines → [{name, path, type, size, mtime}], dirs first.
    The path is the last field, so names may themselves contain '|'."""
    items = []
    for line in (out or "").splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        ftype, size, mtime, path = parts
        path = path.rstrip("\r")
        if not path or path.startswith("find:") or path.startswith("stat:"):
            continue
        name = path.rstrip("/").rsplit("/", 1)[-1]
        try:
            size_i, mtime_i = int(size), int(mtime)
        except ValueError:
            continue
        items.append({"name": name, "path": path,
                      "type": _TYPES.get(ftype, "other"),
                      "size": size_i, "mtime": mtime_i})
    items.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return items


def android_list_cmd(path: str) -> str:
    """Shell command listing one directory level on the device. The trailing
    '/' makes symlinked dirs (e.g. /sdcard) list their contents."""
    p = path if path.endswith("/") else path + "/"
    return ("find %s -maxdepth 1 -mindepth 1 -exec stat -c '%%F|%%s|%%Y|%%n' {} + 2>&1"
            % shq(p))


def android_parent(path: str) -> str:
    p = path.rstrip("/")
    if not p:
        return "/"
    parent = p.rsplit("/", 1)[0]
    return (parent or "") + "/"


class LocalRoot:
    """Local (webui host) filesystem view confined to `root` via realpath."""

    def __init__(self, root: str):
        self.root = os.path.realpath(os.path.expanduser(root))

    def resolve(self, rel: str = "") -> str | None:
        """Path inside the root (relative or absolute-within-root) → real
        absolute path, or None if it would escape the root (`..`, symlinks)."""
        rel = rel or ""
        cand = rel if os.path.isabs(rel) else os.path.join(self.root, rel)
        real = os.path.realpath(cand)
        if real == self.root or real.startswith(self.root + os.sep):
            return real
        return None

    def rel(self, real: str) -> str:
        r = os.path.relpath(real, self.root)
        return "" if r == "." else r

    def list(self, rel: str = "") -> list[dict] | None:
        real = self.resolve(rel)
        if real is None or not os.path.isdir(real):
            return None
        items = []
        for name in os.listdir(real):
            full = os.path.join(real, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            inside = self.resolve(full) is not None
            if not inside:          # symlink that points outside the root
                continue
            items.append({"name": name, "path": self.rel(os.path.realpath(full)),
                          "type": "dir" if os.path.isdir(full) else "file",
                          "size": st.st_size, "mtime": int(st.st_mtime)})
        items.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
        return items


def unique_name(name: str, exists) -> str:
    """`name` if free, else `stem-1.ext`, `stem-2.ext`, … (never overwrite)."""
    if not exists(name):
        return name
    stem, ext = os.path.splitext(name)
    i = 1
    while exists(f"{stem}-{i}{ext}"):
        i += 1
    return f"{stem}-{i}{ext}"


def content_disposition(name: str) -> str:
    """attachment header with an ASCII fallback + RFC 5987 UTF-8 name (Hangul)."""
    from urllib.parse import quote
    ascii_name = name.encode("ascii", "replace").decode("ascii")
    ascii_name = "".join(c if c.isprintable() and c not in '"\\' else "_" for c in ascii_name)
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, quote(name, safe=""))


def clean_upload_name(raw: str) -> str | None:
    """Browser filename → a safe single path component, or None. Keeps the
    original name (Hangul, spaces) — only strips path parts and control chars."""
    from urllib.parse import unquote
    name = unquote(raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isprintable() and c != "\x00").strip()
    if name in ("", ".", ".."):
        return None
    while len(name.encode("utf-8")) > 240:          # stay under NAME_MAX with room for -N
        name = name[:-1]
    return name


def host_allowed(host_header: str, allow_hosts=()) -> bool:
    """DNS-rebinding guard: accept IP-literal hosts (they can't rebind),
    localhost, and explicitly allowed names (e.g. a `tailscale serve` name)."""
    import ipaddress
    h = (host_header or "").strip().lower()
    if not h:
        return False
    if h.startswith("["):                               # [::1]:8800
        h = h[1:h.find("]")] if "]" in h else h[1:]
    elif h.count(":") == 1:                              # name:port / v4:port
        h = h.rsplit(":", 1)[0]
    if h in ("localhost",) or h in {a.lower() for a in allow_hosts}:
        return True
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False
