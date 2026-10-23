// kansei: Web ページを Playwright(Chromium) で実測し、1440px と 375px のスクリーンショットを保存する。
//
// 使い方（Playwright が入っている環境。グローバルに入っている場合は NODE_PATH で場所を教える）:
//   NODE_PATH="$(npm root -g)" timeout 120 node <skill>/scripts/measure-web.mjs <HTMLのパス or URL> --out <保存先> [--name 名前]
//   NODE_PATH="$(npm root -g)" timeout 120 node <skill>/scripts/measure-web.mjs --pair <参考> <制作物> --out <保存先> [--blind]
//   NODE_PATH="$(npm root -g)" timeout 120 node <skill>/scripts/measure-web.mjs --colors <画像.png>
//   NODE_PATH="$(npm root -g)" timeout 120 node <skill>/scripts/measure-web.mjs --selftest
// ブラウザの置き場所が独自なら PLAYWRIGHT_BROWSERS_PATH も渡す。
//
// 出力: <name>-1440.png, <name>-375.png, <name>-measure.json と、標準出力の「再現トークン」（key: 値）。
// 色の面積比は、要素の面積を足すのではなく、スクリーンショットの画素を数えて出す。
// Playwright が無い場合: scripts/measure-page.js をブラウザの JS 実行ツールに貼り、色は scripts/count-colors.py で数える。
import { createRequire } from 'module';
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';
import { resolve, dirname, basename, extname, join } from 'path';
import { fileURLToPath } from 'url';
import { tmpdir } from 'os';

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
let chromium;
try { ({ chromium } = require('playwright')); } catch {
  try { ({ chromium } = require('playwright-core')); } catch {
    console.error('Playwright が見つからない。NODE_PATH="$(npm root -g)" を付けて再実行するか、' +
      'scripts/measure-page.js をブラウザの JS 実行ツールで実行し、色は scripts/count-colors.py で数える。');
    process.exit(3);
  }
}

const args = process.argv.slice(2);
const opt = (name, def) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : def; };
const flag = (name) => args.includes(name);
const WIDTHS = (opt('--widths', '1440,375')).split(',').map(Number);
const pageScript = readFileSync(join(here, 'measure-page.js'), 'utf8');
const toUrl = (t) => /^(https?|file):/.test(t) ? t : 'file://' + resolve(t);
const isImage = (t) => /\.(png|jpe?g|webp)$/i.test(t);

// ---- 画素の集計（ページの canvas に PNG を読み込み getImageData で数える）----
async function countColors(browser, pngBuffer, mime = 'image/png') {
  const page = await browser.newPage();
  try {
    await page.setContent('<canvas id=c></canvas>');
    return await page.evaluate(async ({ b64, mime }) => {
      const img = new Image();
      img.src = `data:${mime};base64,${b64}`;
      await img.decode();
      const c = document.getElementById('c'); c.width = img.naturalWidth; c.height = img.naturalHeight;
      const g = c.getContext('2d'); g.drawImage(img, 0, 0);
      const { data } = g.getImageData(0, 0, c.width, c.height);
      const counts = new Map();
      for (let i = 0; i < data.length; i += 4) {
        const k = ((data[i] >> 2) << 12) | ((data[i + 1] >> 2) << 6) | (data[i + 2] >> 2); // 各色 6bit に丸める
        counts.set(k, (counts.get(k) || 0) + 1);
      }
      const total = data.length / 4;
      const entries = [...counts.entries()].sort((a, b) => b[1] - a[1]);
      const rgb = (k) => [((k >> 12) & 63) * 4 + 2, ((k >> 6) & 63) * 4 + 2, (k & 63) * 4 + 2];
      const clusters = []; // 近い色（距離 24 以内）は、面積の大きい色にまとめる（文字のにじみ等を吸収）
      for (const [k, n] of entries) {
        const c1 = rgb(k);
        const hit = clusters.find(cl => Math.hypot(cl.rgb[0] - c1[0], cl.rgb[1] - c1[1], cl.rgb[2] - c1[2]) <= 24);
        if (hit) hit.n += n; else if (clusters.length < 400) clusters.push({ rgb: c1, n }); else clusters[clusters.length - 1].n += n;
      }
      clusters.sort((a, b) => b.n - a.n);
      const hex = (c) => '#' + c.map(v => Math.min(255, v).toString(16).padStart(2, '0')).join('').toUpperCase();
      return { width: c.width, height: c.height, colors: clusters.slice(0, 12).map(cl => {
        const [r, g2, b] = cl.rgb; const chroma = Math.max(r, g2, b) - Math.min(r, g2, b);
        return { hex: hex(cl.rgb), percent: Math.round(cl.n / total * 1000) / 10, chroma };
      }) };
    }, { b64: pngBuffer.toString('base64'), mime });
  } finally { await page.close(); }
}

function colorTokens(px) {
  const [ground, ...rest] = px.colors;
  const accents = rest.filter(c => c.chroma >= 40 && c.percent >= 0.05);
  const neutrals = rest.filter(c => c.chroma < 40).slice(0, 3);
  return {
    '地色（画素）': `${ground.hex} ${ground.percent}%`,
    '無彩色の次点（画素）': neutrals.map(c => `${c.hex} ${c.percent}%`).join(', ') || 'なし',
    '差し色（画素・彩度のある色）': accents.slice(0, 4).map(c => `${c.hex} ${c.percent}%`).join(', ') || 'なし',
  };
}

// ---- 実際に描画された書体（Chromium の CDP。指定スタックではなく実体）----
async function renderedFonts(page) {
  try {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('DOM.enable'); await cdp.send('CSS.enable');
    const { root } = await cdp.send('DOM.getDocument', { depth: -1 });
    const { nodeIds } = await cdp.send('DOM.querySelectorAll', { nodeId: root.nodeId, selector: '[data-kansei-t]' });
    const tally = {};
    for (const nodeId of nodeIds.slice(0, 150)) {
      const { fonts } = await cdp.send('CSS.getPlatformFontsForNode', { nodeId });
      fonts.forEach(f => { tally[f.familyName] = (tally[f.familyName] || 0) + f.glyphCount; });
    }
    const total = Object.values(tally).reduce((a, b) => a + b, 0) || 1;
    return Object.entries(tally).sort((a, b) => b[1] - a[1]).map(([f, n]) => `${f} ${Math.round(n / total * 100)}%`).join(', ');
  } catch (e) { return `取得できず（${e.message.split('\n')[0]}）`; }
}

async function measure(browser, target, outDir, name) {
  mkdirSync(outDir, { recursive: true });
  const result = { target, measuredAt: new Date().toISOString(), widths: {} };
  for (const w of WIDTHS) {
    const page = await browser.newPage({ viewport: { width: w, height: 900 } });
    try {
      await page.goto(toUrl(target), { waitUntil: 'load', timeout: 30000 });
      await page.evaluate(() => document.fonts && document.fonts.ready);
      await page.waitForTimeout(300);
      const dom = await page.evaluate(pageScript);
      dom.tokens['書体'] = `描画された実体: ${await renderedFonts(page)} ／ 指定スタック上の判定: ${dom.tokens['書体']}`;
      const shot = join(outDir, `${name}-${w}.png`);
      const buf = await page.screenshot({ path: shot, fullPage: true });
      const pixels = await countColors(browser, buf);
      result.widths[w] = { screenshot: shot, tokens: { ...colorTokens(pixels), ...dom.tokens }, pixels, raw: dom.raw };
    } finally { await page.close(); }
  }
  const json = join(outDir, `${name}-measure.json`);
  writeFileSync(json, JSON.stringify(result, null, 2));
  return { result, json };
}

function printTokens({ result, json }) {
  for (const [w, r] of Object.entries(result.widths)) {
    console.log(`\n## ${result.target} @ ${w}px  （スクリーンショット: ${r.screenshot}）`);
    for (const [k, v] of Object.entries(r.tokens)) console.log(`${k}: ${v}`);
  }
  console.log(`\n詳細: ${json}`);
  console.log('注意: 数値は材料。スクリーンショットを開いて目視し、食い違ったら目視を採って「未確認・推定」に書く。');
}

// ---- 並べた1枚（参考｜制作物）----
async function pair(browser, a, b, outDir, blind) {
  mkdirSync(outDir, { recursive: true });
  let items = [{ src: a, role: '1つ目に渡したもの' }, { src: b, role: '2つ目に渡したもの' }];
  if (blind && Math.random() < 0.5) items = items.reverse();
  const outs = [];
  for (const w of (items.every(i => isImage(i.src)) ? [0] : WIDTHS)) {
    const bufs = [];
    for (const it of items) {
      if (isImage(it.src)) { bufs.push(readFileSync(it.src)); continue; }
      const page = await browser.newPage({ viewport: { width: w, height: 900 } });
      await page.goto(toUrl(it.src), { waitUntil: 'load', timeout: 30000 });
      await page.waitForTimeout(300);
      bufs.push(await page.screenshot({ fullPage: true })); await page.close();
    }
    const labels = blind ? ['左', '右'] : items.map(i => basename(i.src));
    const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
    const imgs = bufs.map(x => `data:image/png;base64,${x.toString('base64')}`);
    await page.setContent(`<style>body{margin:0;background:#888;font:16px sans-serif;color:#fff}
      .row{display:flex;gap:24px;padding:16px;align-items:flex-start}.c{flex:1;min-width:0}.c img{width:100%;display:block;background:#fff}
      .c p{margin:0 0 8px}</style><div class=row>${imgs.map((s, i) => `<div class=c><p>${labels[i]}</p><img src="${s}"></div>`).join('')}</div>`);
    await page.evaluate(() => Promise.all([...document.images].map(i => i.decode())));
    const out = join(outDir, `pair${w ? '-' + w : ''}.png`);
    await page.screenshot({ path: out, fullPage: true }); await page.close();
    outs.push(out);
  }
  if (blind) writeFileSync(join(outDir, 'pair-key.txt'), `左=${items[0].src}\n右=${items[1].src}\n`);
  console.log(`並べた1枚: ${outs.join(', ')}${blind ? `（どちらが何かは ${join(outDir, 'pair-key.txt')}。講評者には見せない）` : ''}`);
}

async function selftest(browser) {
  const html = join(here, 'selftest.html');
  const outDir = opt('--out', join(tmpdir(), 'kansei-selftest'));
  const { result } = await measure(browser, html, outDir, 'selftest');
  const r = result.widths[WIDTHS[0]];
  const [ground] = r.pixels.colors;
  const accent = r.pixels.colors.find(c => c.chroma >= 40);
  const near = (hex, target) => { const p = (h) => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)); const [x, y] = [p(hex), p(target)]; return Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]) <= 12; };
  const checks = [
    ['地色（html に指定、body は透明）が画素で最大', near(ground.hex, '#EAE3D6') && ground.percent >= 60, `${ground.hex} ${ground.percent}%`],
    ['差し色 #B5452B が少量（0.5〜8%）として出る', !!accent && near(accent.hex, '#B5452B') && accent.percent >= 0.5 && accent.percent <= 8, accent ? `${accent.hex} ${accent.percent}%` : 'なし'],
    ['本文サイズ 16px', r.raw.bodySize === 16, `${r.raw.bodySize}px`],
    ['縦書きを検出', r.raw.vertical.length === 1, r.raw.vertical.join(',')],
    ['カード 0・固定ヘッダーなし', r.raw.cards === 0 && r.raw.pinned.length === 0, `cards=${r.raw.cards} pinned=${r.raw.pinned.length}`],
    ['描画された書体が取れる', !/取得できず/.test(r.tokens['書体']), r.tokens['書体'].slice(0, 80)],
  ];
  let ok = true;
  for (const [name, pass, got] of checks) { console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}  （実測: ${got}）`); ok &&= pass; }
  console.log(ok ? 'selftest: すべて PASS' : 'selftest: FAIL あり。数値を信用せず、スクリーンショットの目視を主にする');
  return ok;
}

const browser = await chromium.launch();
let code = 0;
try {
  if (flag('--selftest')) code = (await selftest(browser)) ? 0 : 1;
  else if (flag('--pair')) { const i = args.indexOf('--pair'); await pair(browser, args[i + 1], args[i + 2], opt('--out', '.'), flag('--blind')); }
  else if (flag('--colors')) {
    const f = opt('--colors'); const mime = /\.jpe?g$/i.test(f) ? 'image/jpeg' : /\.webp$/i.test(f) ? 'image/webp' : 'image/png';
    const px = await countColors(browser, readFileSync(f), mime);
    console.log(`${f} ${px.width}x${px.height}`); px.colors.forEach(c => console.log(`${c.hex} ${c.percent}% 彩度差${c.chroma}`));
    for (const [k, v] of Object.entries(colorTokens(px))) console.log(`${k}: ${v}`);
  } else {
    const target = args.find(a => !a.startsWith('--') && args[args.indexOf(a) - 1] !== '--out' && args[args.indexOf(a) - 1] !== '--name' && args[args.indexOf(a) - 1] !== '--widths');
    if (!target) { console.error('usage: measure-web.mjs <html|url> --out <dir> [--name n] | --pair a b --out dir [--blind] | --colors img | --selftest'); code = 2; }
    else {
      const name = opt('--name', basename(target, extname(target)) || 'page');
      if (!existsSync(target) && !/^(https?|file):/.test(target)) { console.error(`見つからない: ${target}`); code = 2; }
      else printTokens(await measure(browser, target, opt('--out', '.'), name));
    }
  }
} finally { await browser.close(); }
process.exit(code);
