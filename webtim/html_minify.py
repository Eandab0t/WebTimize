"""HTML minification through htmlmin.

htmlmin 0.1.12 does `import cgi` and never calls it. Python 3.13 removed the stdlib `cgi` module,
so the import fails before htmlmin can run. A minimal stand-in (escape() matches the old
cgi.escape semantics) is installed only when the real module is gone; nothing else changes.
"""
import sys
import types


def _cgi_stand_in():
    """The part of `cgi` that existed for legacy callers; htmlmin only needs the module to import."""
    module = types.ModuleType("cgi")
    module.__doc__ = "Minimal stand-in for the stdlib cgi module removed in Python 3.13."

    def escape(s, quote=False):
        s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return s.replace('"', "&quot;") if quote else s

    module.escape = escape
    return module


try:  # Python <= 3.12
    import cgi  # noqa: F401
except ImportError:  # Python 3.13+
    sys.modules["cgi"] = _cgi_stand_in()

import htmlmin

# htmlmin has no optional-closing-tags option (the old kwarg raised TypeError); comments stay unless asked.
_MINIFIER = htmlmin.Minifier(remove_comments=True)


def minify(html: str) -> str:
    return _MINIFIER.minify(html)
