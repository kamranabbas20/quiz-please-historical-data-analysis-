"""Bundle the dashboard into one self-contained HTML file.

The served app loads its CSS, its ES modules and its per-city datasets over
HTTP. That is the right shape for a deployed site, but it means the app cannot
be opened from disk (browsers block `fetch` on `file://`) or handed to someone
as a single file. This flattens all of it into one document: the stylesheet
inline, the modules concatenated in dependency order with their import/export
lines stripped, and the datasets embedded as `window.__QP_DATA__`, which
`app/js/data.js` uses in place of fetching.

    python3 -m dashboard.bundle -o dist/dashboard.html
"""

import argparse
import json
import os
import re
import sys

__all__ = ["build_bundle"]

APP_DIR = "app"
# Dependency order: data and stats are leaves, charts is standalone, ui uses all.
MODULES = ("js/data.js", "js/stats.js", "js/charts.js", "js/ui.js")

_IMPORT = re.compile(r"^import\s[^;]*?;\s*$", re.M | re.S)
_EXPORT = re.compile(r"^export\s+(?=(?:const|function|class|async|let|var)\b)", re.M)
_EXPORT_LIST = re.compile(r"^export\s*\{[^}]*\}\s*;?\s*$", re.M)


def _strip_module_syntax(source):
    """Turn an ES module into a plain script body.

    Every module here exports declarations and imports only from its siblings,
    so concatenation in dependency order is equivalent to the module graph.
    """
    source = _IMPORT.sub("", source)
    source = _EXPORT_LIST.sub("", source)
    return _EXPORT.sub("", source)


def _json_for_script(value):
    """JSON safe to inline in a <script>: no tag-closing sequences."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def build_bundle(app_dir=APP_DIR, out_file=os.path.join("dist", "dashboard.html"),
                 data_dir=None, fragment=False, title=None):
    """Write the bundle. ``fragment`` omits the document skeleton, for hosts
    that supply their own <head>/<body> wrapper."""
    data_dir = data_dir or os.path.join(app_dir, "data")

    with open(os.path.join(data_dir, "index.json"), encoding="utf-8") as handle:
        index = json.load(handle)
    cities = {}
    for entry in index["cities"]:
        with open(os.path.join(data_dir, entry["file"]), encoding="utf-8") as handle:
            cities[entry["slug"]] = json.load(handle)

    with open(os.path.join(app_dir, "index.html"), encoding="utf-8") as handle:
        html = handle.read()
    with open(os.path.join(app_dir, "css", "app.css"), encoding="utf-8") as handle:
        css = handle.read()

    # The logo ships inline in the page, so the single file needs no assets.
    logo_path = os.path.join(app_dir, "assets", "logo-quizplease.svg")
    if os.path.exists(logo_path):
        with open(logo_path, encoding="utf-8") as handle:
            logo = handle.read()
        html = html.replace('<span class="brand-logo" data-src="assets/logo-quizplease.svg"></span>',
                            '<span class="brand-logo">%s</span>' % logo)

    body = html[html.index("<body>") + len("<body>"):html.index("</body>")]
    # Drop the module bootstrap; the bundle calls start() itself at the end.
    body = re.sub(r'<script type="module">.*?</script>', "", body, flags=re.S).strip()

    script = "\n".join(
        _strip_module_syntax(open(os.path.join(app_dir, name), encoding="utf-8").read())
        for name in MODULES
    )

    found = re.search(r"<title>(.*?)</title>", html, re.S)
    page_title = title or (found.group(1) if found else "Quiz Please")

    if fragment:
        document = """<title>%s</title>
<style>
%s
</style>
%s
<script>window.__QP_DATA__ = {"index": %s, "cities": %s};</script>
<script>
%s
start();
</script>
""" % (page_title, css, body, _json_for_script(index), _json_for_script(cities), script)
        directory = os.path.dirname(os.path.abspath(out_file))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as handle:
            handle.write(document)
        return out_file, len(document.encode("utf-8"))

    document = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title>
<style>
%s
</style>
</head>
<body>
%s
<script>window.__QP_DATA__ = {"index": %s, "cities": %s};</script>
<script>
%s
start();
</script>
</body>
</html>
""" % (page_title, css, body,
       _json_for_script(index), _json_for_script(cities), script)

    directory = os.path.dirname(os.path.abspath(out_file))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as handle:
        handle.write(document)
    return out_file, len(document.encode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m dashboard.bundle",
                                     description=__doc__.split("\n")[0])
    parser.add_argument("--app-dir", default=APP_DIR)
    parser.add_argument("-o", "--out-file",
                        default=os.path.join("dist", "quiz-please-baku.html"))
    parser.add_argument("--fragment", action="store_true",
                        help="omit <html>/<head>/<body>, for a host that wraps the page")
    parser.add_argument("--title", help="override the page title")
    args = parser.parse_args(argv)

    path, size = build_bundle(args.app_dir, args.out_file,
                              fragment=args.fragment, title=args.title)
    print("wrote %s (%.1f MB, opens with no server)" % (path, size / 1024.0 / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
