// Презентация в PDF: node tools/build_presentation.js
//
// Слайды — docs/presentation/source/slides/<id>.html в порядке из deck.json, 1920×1080;
// картинки указаны путём от корня репозитория. Результат — docs/presentation/<deck.pdf>.
// Нужен Playwright с Chromium.

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = path.join(__dirname, "..");
const SOURCE = path.join(ROOT, "docs", "presentation", "source");

const ICONS = {    // <x-icon name="…"> → простые значки (контур 24×24)
  Search: '<circle cx="10" cy="10" r="6"/><path d="M15 15l6 6"/>',
  Database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  Lightbulb: '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-4 10.5c.8.8 1 1.5 1 2.5h6c0-1 .2-1.7 1-2.5A6 6 0 0 0 12 3z"/>',
  Settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
  Link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  Globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>',
  Book: '<path d="M4 4h6a2 2 0 0 1 2 2v14a2 2 0 0 0-2-2H4zM20 4h-6a2 2 0 0 0-2 2v14a2 2 0 0 1 2-2h6z"/>',
  Check: '<path d="M4 12l5 5L20 6"/>',
  Clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  Star: '<path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z"/>',
  Users: '<circle cx="9" cy="8" r="3.5"/><path d="M2 20c0-3.9 3.1-6 7-6s7 2.1 7 6"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14c2.4.6 4 2.5 4 6"/>',
  PaperPlane: '<path d="M21 3L3 10l7 3 3 7z"/><path d="M10 13l11-10"/>',
  Wrench: '<path d="M14 6a4 4 0 0 0 5 5l-9 9a2 2 0 0 1-3-3l9-9a4 4 0 0 0-2-2z"/>',
  Play: '<path d="M7 4l13 8-13 8z"/>',
};

function slideHtml(text) {
  return text
    .replace(/<aside>[\s\S]*?<\/aside>/g, "")
    .replace(/src="(?!https?:|file:|data:)([^"]+)"/g, (match, rel) => {
      const file = path.join(ROOT, rel);
      if (!fs.existsSync(file)) throw new Error("Нет картинки " + rel);
      return 'src="file://' + file + '"';
    })
    .replace(/<x-icon name="(\w+)" style="([^"]*)"><\/x-icon>/g, (match, name, style) =>
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round" style="' + style + '">' +
      (ICONS[name] || "") + "</svg>");
}

const deck = JSON.parse(fs.readFileSync(path.join(SOURCE, "deck.json"), "utf8"));
const fonts = Object.values(deck.faces || {}).map((f) => '<link rel="stylesheet" href="' + f.href + '">');
const slides = deck.order.map((id) =>
  slideHtml(fs.readFileSync(path.join(SOURCE, "slides", id + ".html"), "utf8")));
const html = `<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>${deck.title}</title>${fonts.join("")}
<style>
@page { size: 1920px 1080px; margin: 0 }
* { margin: 0; box-sizing: border-box }
section { width: 1920px; height: 1080px; position: relative; overflow: hidden; break-after: page }
h1, h2, h3 { font-weight: 600 }
p { line-height: 1.4 }
ul, ol { padding-left: 1.3em }
li { margin-bottom: 0.35em }
table { border-collapse: collapse }
th, td { text-align: left; vertical-align: top; padding: 0.35em 0.6em; border-bottom: 1px solid rgba(0,0,0,.15) }
th { font-weight: 600; border-bottom: 2px solid currentColor }
</style></head><body>${slides.join("\n")}</body></html>`;
const temp = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "deck-")), "deck.html");
fs.writeFileSync(temp, html);
const out = path.join(ROOT, "docs", "presentation", deck.pdf);
execFileSync("node", [path.join(__dirname, "html2pdf.js"), temp, out, "slides"], { stdio: "inherit" });
