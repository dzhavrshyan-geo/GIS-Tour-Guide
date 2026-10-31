# -*- coding: utf-8 -*-
"""Инструкция в PDF: python tools/build_docs.py  →  docs/ИНСТРУКЦИЯ.pdf

Берёт docs/ИНСТРУКЦИЯ.md, оформляет (обложка, оглавление, цвета плагина) и печатает в PDF
через Chromium (tools/html2pdf.js). Нужны: pip install markdown; Node.js и Playwright.
Настройки оформления — docs/docs.json.
"""

import html
import json
import os
import re
import subprocess
import sys
import tempfile

import markdown

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DOCS = os.path.join(ROOT, "docs")


def version(metadata):
    with open(os.path.join(ROOT, metadata), encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("version="):
                return line.split("=", 1)[1].strip()
    return "?"


def absolute_images(body):
    """Пути картинок в инструкции — от папки docs/; для печати — file://…"""
    def fix(match):
        src = match.group(2)
        if re.match(r"^(https?|file|data):", src):
            return match.group(0)
        return '{}="file://{}"'.format(match.group(1), os.path.abspath(os.path.join(DOCS, src)))
    return re.sub(r'(src)="([^"]+)"', fix, body)


LIST_ITEM = re.compile(r"^(\s*)([*+-]|\d+\.)\s")


def github_lists(text):
    """Списки, как их понимает GitHub, — для строгого Python-Markdown: пустая строка перед
    списком после абзаца и отступ вложенных строк кратно 4 пробелам (у GitHub — 2–3)."""
    result = []
    fenced = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if fenced or line.lstrip().startswith("```"):
            result.append(line)              # блок кода — как есть
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent and line.strip():
            level = indent // 2 if indent % 3 else indent // 3
            line = " " * (4 * max(level, 1)) + line.lstrip(" ")
        previous = result[-1] if result else ""
        if LIST_ITEM.match(line) and previous.strip() and not LIST_ITEM.match(previous):
            result.append("")
        result.append(line)
    return "\n".join(result)


def build():
    with open(os.path.join(DOCS, "docs.json"), encoding="utf-8") as handle:
        cfg = json.load(handle)
    with open(os.path.join(DOCS, "ИНСТРУКЦИЯ.md"), encoding="utf-8") as handle:
        text = handle.read()
    text = re.sub(r"^# .*\n", "", text, count=1)              # заголовок — на обложке
    text = re.sub(r"\n## Содержание\n(?:\n|\d+\. .*\n)*", "\n", text, count=1)
    text = re.sub(r"^.*· версия .*\n", "", text, count=1, flags=re.M)   # уже на обложке
    text = github_lists(text)
    md = markdown.Markdown(extensions=["tables", "toc", "attr_list", "md_in_html", "fenced_code"],
                           extension_configs={"toc": {"toc_depth": "2-2"}})
    body = absolute_images(md.convert(text))
    toc = md.toc
    accent, dark, light = cfg["accent"], cfg["dark"], cfg["light"]
    logo = cfg.get("logo")
    logo_html = '<img class="logo" src="file://{}">'.format(
        os.path.abspath(os.path.join(ROOT, logo))) if logo else ""
    org = "".join("<div class='org'>{}</div>".format(html.escape(line))
                  for line in cfg.get("org", []))
    page = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>{title} — инструкция</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&family=Rubik:wght@500;600&display=swap">
<style>
body {{ font-family: 'IBM Plex Sans', Arial, sans-serif; color: {dark}; font-size: 10.5pt;
       line-height: 1.5; margin: 0 }}
h1, h2, h3 {{ font-family: 'Rubik', Arial, sans-serif; color: {dark}; line-height: 1.2 }}
h2 {{ font-size: 17pt; margin: 0 0 10pt; padding-top: 4pt; border-top: 3pt solid {accent};
      break-before: page }}
h3 {{ font-size: 12.5pt; margin: 14pt 0 6pt }}
p {{ margin: 0 0 7pt }}
ol, ul {{ margin: 0 0 8pt; padding-left: 18pt }}
li {{ margin-bottom: 3pt }}
img {{ max-width: 100%; height: auto; border: 1px solid #d0d5cc; border-radius: 4px;
       break-inside: avoid }}
p > img:only-child {{ display: block; margin: 6pt 0 2pt }}
em {{ color: #555 }}
code {{ background: {light}; padding: 0 3pt; border-radius: 3px; font-size: 9.5pt }}
pre {{ background: {light}; padding: 8pt 10pt; border-radius: 4px; font-size: 8.5pt;
       line-height: 1.35; white-space: pre-wrap }}
pre code {{ background: none; padding: 0 }}
table {{ border-collapse: collapse; margin: 6pt 0 10pt; width: 100% }}
th, td {{ border-bottom: 1px solid #d0d5cc; padding: 4pt 6pt; text-align: left;
          vertical-align: top }}
th {{ border-bottom: 2px solid {dark} }}
.cover {{ height: 255mm; display: flex; flex-direction: column; justify-content: center;
          gap: 14pt; padding: 0 6mm; break-after: page }}
.cover .logo {{ border: none; width: {logo_width}; margin-bottom: 10pt }}
.cover .org {{ color: {accent}; font-size: 12pt; font-weight: 600; letter-spacing: 1pt }}
.cover h1 {{ font-size: 34pt; margin: 6pt 0 0 }}
.cover .subtitle {{ font-size: 14pt; color: #444; max-width: 150mm }}
.cover .meta {{ font-size: 10pt; color: #777; margin-top: 18pt }}
.cover .bar {{ width: 60mm; height: 5pt; background: {accent} }}
.toc h2 {{ break-before: auto }}
.toc ul {{ list-style: none; padding-left: 0; font-size: 12pt }}
.toc li {{ margin-bottom: 6pt }}
.toc a {{ color: {dark}; text-decoration: none }}
a {{ color: {link} }}
</style></head><body>
<div class="cover">
  {logo_html}
  {org}
  <h1>{title}</h1>
  <div class="bar"></div>
  <div class="subtitle">{subtitle}</div>
  <div class="meta">Инструкция по использованию · версия {version}<br>{authors}</div>
</div>
<div class="toc"><h2>Содержание</h2>{toc}</div>
{body}
</body></html>""".format(
        title=html.escape(cfg["title"]), subtitle=html.escape(cfg["subtitle"]),
        version=version(cfg["metadata"]), authors=html.escape(cfg["authors"]),
        accent=accent, dark=dark, light=light, link=cfg.get("link", accent),
        logo_width=cfg.get("logo_width", "38mm"), logo_html=logo_html, org=org,
        toc=toc, body=body)
    temp = os.path.join(tempfile.mkdtemp(), "guide.html")
    with open(temp, "w", encoding="utf-8") as handle:
        handle.write(page)
    out = os.path.join(DOCS, "ИНСТРУКЦИЯ.pdf")
    subprocess.run(["node", os.path.join(ROOT, "tools", "html2pdf.js"), temp, out], check=True)
    return out


if __name__ == "__main__":
    sys.exit(0 if build() else 1)
