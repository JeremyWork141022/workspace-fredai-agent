# Local KaTeX Vendor Folder

Put the approved KaTeX browser distribution files here.

Minimum files/folders expected by the UI:

```text
web/vendor/katex/katex.min.js
web/vendor/katex/katex.min.css
web/vendor/katex/fonts/
```

The browser loads them from:

```text
/static/vendor/katex/katex.min.js
/static/vendor/katex/katex.min.css
```

`fonts/` is needed because `katex.min.css` references KaTeX web fonts. Without
the fonts, formulas may render with missing symbols or poor layout.

If KaTeX is not present, formulas remain visible as TeX-style text, but they are
not visually typeset.
