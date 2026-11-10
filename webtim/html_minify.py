"""HTML minification through the maintained htmlmin2 distribution."""

import htmlmin

# htmlmin has no optional-closing-tags option (the old kwarg raised TypeError); comments stay unless asked.
_MINIFIER = htmlmin.Minifier(remove_comments=True)


def minify(html: str) -> str:
    return _MINIFIER.minify(html)
