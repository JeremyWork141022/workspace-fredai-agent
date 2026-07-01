# Local MathJax Vendor Folder

Place the approved MathJax browser bundle here when formula rendering is allowed
on the work computer.

Expected file:

```text
web/vendor/mathjax/tex-mml-chtml.js
```

The UI loads it from:

```text
/static/vendor/mathjax/tex-mml-chtml.js
```

If the file is not present, formulas remain visible as plain TeX-style text
inside the chat. No CDN, npm install, or external network call is required.

Recommended source to approve internally:

```text
MathJax v3 tex-mml-chtml browser component
```
