// HTML → PDF через Chromium: node tools/html2pdf.js страница.html результат.pdf [slides]
// slides — страницы 1920×1080 без полей (презентация), иначе A4 с номерами страниц.
// Нужен Playwright с Chromium (npm install playwright && npx playwright install chromium).

const path = require("path");
const { chromium } = require("playwright");

(async () => {
  const [input, output, mode] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto("file://" + path.resolve(input), { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  if (mode === "slides") {
    await page.pdf({ path: output, width: "1920px", height: "1080px", printBackground: true });
  } else {
    await page.pdf({
      path: output, format: "A4", printBackground: true,
      margin: { top: "18mm", bottom: "18mm", left: "16mm", right: "16mm" },
      displayHeaderFooter: true, headerTemplate: "<span></span>",
      footerTemplate: '<div style="width:100%;font-size:8px;color:#888;text-align:center">' +
        '<span class="pageNumber"></span> / <span class="totalPages"></span></div>',
    });
  }
  await browser.close();
  console.log(output);
})();
