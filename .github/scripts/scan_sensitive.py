#!/usr/bin/env python3
"""Fail if tracked text contains data that must not be public.

Built-in rules are generic. Project-specific forbidden words are NOT in this file (the repo is
public): put one regex per line in the untracked `.sensitive-patterns` file at the repo root, and/or
in the SENSITIVE_PATTERNS environment variable (newline separated, e.g. a GitHub secret).

Modes:
  (default)        every tracked file in the working tree
  --staged         the staged blobs (pre-commit)
  --range "A..B"   lines added by the commits in that git-log range (pre-push); the value is passed
                   to `git log`, so "HEAD --not --remotes" works too

Output is `file:line  rule` only; matched text is never printed. Exit code 1 on any hit.
"""
import argparse
import os
import re
import subprocess
import sys

BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".mp4", ".zip", ".dll", ".exe", ".pdb",
              ".nupkg", ".snk", ".pfx", ".woff", ".woff2", ".pdf", ".msp"}

# C:\ locations that are documented generic examples
C_ALLOWED = re.compile(r"[Cc]:[\\/]+(Program Files( \(x86\))?|Windows|Temp|ProgramData|x\.|mon\.exe|cae_prog[\\/]AVEVA|Users[\\/]+(<|%|\{|\$|Public|Default|\.\.\.|YOU|you|username|USERNAME|name|x\b))", re.I)

RULES = [
    ("dbref-5-digit-db", re.compile(r"=\d{5,}/\d+")),
    ("user-profile-path", re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?![<%{$]|Public\b|Default\b|\.\.\.|username\b|YOU\b|name\b|x\b)[^\\/\s\"'<>|:*?]+", re.I)),
    ("non-C-drive-path", re.compile(r"(?<![A-Za-z0-9])[D-Zd-z]:[\\/]{1,2}(?=[A-Za-z0-9_$%.{<\-])")),
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")),
]
EMAIL_OK = re.compile(r"noreply|no-reply|@example\.(com|org|net)$|@users\.noreply\.github\.com$", re.I)


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, check=True)
    return r.stdout


def repo_root():
    return git("rev-parse", "--show-toplevel").decode().strip()


def load_extra(root):
    lines = []
    path = os.path.join(root, ".sensitive-patterns")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            lines += f.read().splitlines()
    lines += os.environ.get("SENSITIVE_PATTERNS", "").splitlines()
    out = []
    for i, ln in enumerate((l.strip() for l in lines if l.strip() and not l.strip().startswith("#")), 1):
        try:
            out.append((f"extra-pattern-{i}", re.compile(ln, re.I)))
        except re.error:
            print(f"warning: extra pattern #{i} is not a valid regex, ignored", file=sys.stderr)
    return out


def check_line(text, rules):
    hits = []
    for name, rx in rules:
        for m in rx.finditer(text):
            tok = m.group(0)
            if name == "email" and EMAIL_OK.search(tok):
                continue
            if name == "non-C-drive-path":
                pass
            hits.append(name)
            break
    # C:\ paths that are not documented generic locations
    for m in re.finditer(r"(?<![A-Za-z0-9])[Cc]:[\\/]+[A-Za-z0-9_$%.{<\-]", text):
        if not C_ALLOWED.match(text, m.start()) and "user-profile-path" not in hits:
            hits.append("c-drive-path-not-generic")
            break
    return hits


def is_text(data):
    return b"\0" not in data[:8000]


def scan_blob(path, data, rules, hits):
    if os.path.splitext(path)[1].lower() in BINARY_EXT or not is_text(data):
        return
    for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        for r in check_line(line, rules):
            hits.append((path, n, r))


def scan_tree(rules, hits, staged):
    if staged:
        names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").decode().split("\0")
    else:
        names = git("ls-files", "-z").decode().split("\0")
    root = repo_root()
    for p in filter(None, names):
        try:
            data = git("show", ":" + p) if staged else open(os.path.join(root, p), "rb").read()
        except (OSError, subprocess.CalledProcessError):
            continue
        scan_blob(p, data, rules, hits)


def scan_range(spec, rules, hits):
    out = git("log", "-p", "-U0", "--no-color", "--no-ext-diff", "--format=", *spec.split()).decode("utf-8", "replace")
    path, lineno, skip = None, 0, False
    for line in out.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            skip = path is None or os.path.splitext(path)[1].lower() in BINARY_EXT
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
        elif line.startswith("+") and not line.startswith("+++") and not skip:
            for r in check_line(line[1:], rules):
                hits.append((path, lineno, r))
            lineno += 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--staged", action="store_true")
    g.add_argument("--range", metavar="SPEC")
    a = ap.parse_args()
    root = repo_root()
    os.chdir(root)
    rules = RULES + load_extra(root)
    hits = []
    if a.range:
        scan_range(a.range, rules, hits)
    else:
        scan_tree(rules, hits, a.staged)
    seen = set()
    for path, n, rule in hits:
        if (path, n, rule) not in seen:
            seen.add((path, n, rule))
            print(f"{path}:{n}  {rule}")
    print(f"scan_sensitive: {len(seen)} hit(s)", file=sys.stderr)
    return 1 if seen else 0


if __name__ == "__main__":
    sys.exit(main())
