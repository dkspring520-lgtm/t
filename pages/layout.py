"""Small shared layout injector for Rabbit Quant work pages.

UI assets live here instead of in the compatibility templates held by
``app_core.py``.  Trading and account code deliberately do not pass through
this module.
"""

from __future__ import annotations

import re


_STYLE_MARKER = "rq-shared-layout-assets"
_SCRIPT_MARKER = "rq-shared-layout-scripts"
_STYLES = """<!-- rq-shared-layout-assets -->
<link rel="stylesheet" href="/assets/app-navigation.css?v=20260711b" />
<link rel="stylesheet" href="/assets/layout-unified.css?v=20260711b" />
"""
_SCRIPTS = """<!-- rq-shared-layout-scripts -->
<script defer src="/assets/app-navigation.js?v=20260711b"></script>
<script defer src="/assets/layout-unified.js?v=20260711b"></script>
"""


def _inject_before(html: str, closing_tag: str, fragment: str) -> str:
    index = html.lower().rfind(closing_tag)
    return html + fragment if index < 0 else html[:index] + fragment + html[index:]


def _mark_body(html: str, page: str) -> str:
    page_class = f"rq-page-{re.sub(r'[^a-z0-9-]', '', page.lower()) or 'app'}"

    def replace(match: re.Match[str]) -> str:
        attrs = match.group(1) or ""
        class_match = re.search(r'\bclass\s*=\s*(["\'])(.*?)\1', attrs, re.I | re.S)
        if class_match:
            classes = class_match.group(2).split()
            if page_class not in classes:
                classes.append(page_class)
            attrs = attrs[: class_match.start()] + f'class={class_match.group(1)}{" ".join(classes)}{class_match.group(1)}' + attrs[class_match.end() :]
        else:
            attrs += f' class="{page_class}"'
        return f"<body{attrs}>"

    return re.sub(r"<body\b([^>]*)>", replace, html, count=1, flags=re.I | re.S)


def inject_app_layout(html: str, page: str) -> str:
    """Attach the common navigation and responsive assets exactly once."""
    if not isinstance(html, str) or not html.strip():
        return html
    result = _mark_body(html, page)
    if _STYLE_MARKER not in result:
        result = _inject_before(result, "</head>", _STYLES)
    if _SCRIPT_MARKER not in result:
        result = _inject_before(result, "</body>", _SCRIPTS)
    return result
