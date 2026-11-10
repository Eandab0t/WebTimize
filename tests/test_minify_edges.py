import subprocess
import sys
from pathlib import Path

from webtim.css_minify import minify as minify_css
from webtim.html_minify import _cgi_stand_in, minify as minify_html
from webtim.js_minify import minify as minify_js

ROOT = Path(__file__).resolve().parents[1]


def test_html_keeps_pre_and_textarea_verbatim():
    assert minify_html("<pre>  a\n   b  </pre>") == "<pre>  a\n   b  </pre>"
    assert minify_html("<textarea>  a\n  b </textarea>") == "<textarea>  a\n  b </textarea>"


def test_html_keeps_conditional_comments():
    html = "<!--[if IE]><p>x</p><![endif]--><p>y</p>"
    assert minify_html(html) == html


def test_html_keeps_exactly_one_space_between_inline_elements():
    assert minify_html("<span>a</span>   <span>b</span>") == "<span>a</span> <span>b</span>"


def test_html_attribute_values_containing_gt_survive():
    assert 'data-x="a>b"' in minify_html('<div data-x="a>b">t</div>')


def test_html_does_not_touch_inline_script_source():
    html = '<script>var a = 1 < 2 && "x";</script>'
    assert minify_html(html) == html


def test_css_keeps_calc_spacing():
    assert "calc(100% - 10px)" in minify_css("a { width: calc(100% - 10px); }")
    assert "calc(100% - var(--gap))" in minify_css("a { width: calc(100% - var(--gap)); }")


def test_css_keeps_minmax_and_url_paths():
    result = minify_css("a { grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }")
    assert "minmax(200px,1fr)" in result
    assert "img/x.png" in minify_css('a { background: url("img/x.png"); }')


def test_css_keeps_important_and_media_queries():
    result = minify_css("@media (min-width: 768px) { .a { color: red !important; } }")
    assert "768" in result and "!important" in result


def test_js_with_a_nested_template_literal_is_left_unminified():
    src = "const a = `x ${ `y ${1}` }`; console.log(a);"
    assert minify_js(src) == src


def test_cgi_stand_in_matches_legacy_escape_semantics():
    module = _cgi_stand_in()
    assert module.escape('<a href="x">&') == '&lt;a href="x"&gt;&amp;'
    assert module.escape('<a href="x">', quote=True) == '&lt;a href=&quot;x&quot;&gt;'


def test_html_minify_imports_when_the_stdlib_cgi_module_is_gone():
    """Simulate 3.13: block `import cgi` entirely in a fresh interpreter, then minify HTML."""
    code = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'cgi':\n"
        "            raise ModuleNotFoundError(\"No module named 'cgi'\")\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        "import webtim.html_minify as hm\n"
        "print(hm.minify('<!-- c --><div>hi</div>'))\n"
        "print('shim' if 'stand-in' in (sys.modules['cgi'].__doc__ or '') else 'wrong')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert "<div>hi</div>" in result.stdout
    assert "shim" in result.stdout
