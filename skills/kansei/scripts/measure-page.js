// kansei: Web ページの「必須の軸」を DOM から測る関数。
// 使い方は2通り（中身は同じ）:
//   1) scripts/measure-web.mjs が Playwright で読み込んで実行する（通常はこちら）
//   2) Playwright が無いとき: このファイルの中身をそのままブラウザの JavaScript 実行ツールに貼って実行する。
//      戻り値の tokens を「再現トークン」の材料にする。色の面積比はこの関数では出さない
//      （要素の面積を足すと重なりで狂うため）。色はスクリーンショットを scripts/count-colors.py で数える。
// 値はすべて「材料」。スクリーンショットの目視と食い違ったら目視を採り、食い違いを記録する。
(() => {
  const cs = (e) => getComputedStyle(e);
  const px = (v) => parseFloat(v) || 0;
  const round = (n, d = 1) => Math.round(n * 10 ** d) / 10 ** d;
  const toHex = (c) => { const m = c.match(/[\d.]+/g); if (!m) return c; const h = m.slice(0, 3).map(v => (+v).toString(16).padStart(2, '0')).join('').toUpperCase(); return `#${h}${m[3] !== undefined && +m[3] < 1 ? ` α${m[3]}` : ''}`; };
  const vw = innerWidth, vh = innerHeight;
  const pageHeight = Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0);
  const visible = (e) => {
    const r = e.getBoundingClientRect(); const s = cs(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && px(s.opacity) !== 0;
  };
  const all = [...document.querySelectorAll('body, body *')].filter(visible);
  const docTop = (r) => r.top + scrollY;

  // ---- 文字のかたまり（直下にテキストを持つ要素）----
  const ownText = (e) => [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').replace(/\s+/g, ' ').trim();
  const blocks = [];
  all.forEach((e, i) => {
    if (['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(e.tagName)) return;
    const t = ownText(e);
    if (!t) return;
    const range = document.createRange(); range.selectNodeContents(e);
    const rects = [...range.getClientRects()].filter(r => r.width > 0 && r.height > 0);
    if (!rects.length) return;
    const u = rects.reduce((a, r) => ({ l: Math.min(a.l, r.left), r: Math.max(a.r, r.right), t: Math.min(a.t, r.top), b: Math.max(a.b, r.bottom) }),
      { l: Infinity, r: -Infinity, t: Infinity, b: -Infinity });
    const s = cs(e);
    e.setAttribute('data-kansei-t', String(blocks.length));
    blocks.push({ el: e, text: t, chars: [...t].length, u, s, size: px(s.fontSize), lh: s.lineHeight === 'normal' ? px(s.fontSize) * 1.2 : px(s.lineHeight) });
  });
  const totalChars = blocks.reduce((a, b) => a + b.chars, 0) || 1;
  const weighted = (fn) => {
    const m = {}; blocks.forEach(b => { const k = fn(b); if (k == null) return; m[k] = (m[k] || 0) + b.chars; });
    return Object.entries(m).sort((a, b) => b[1] - a[1]).map(([k, v]) => [k, round(v / totalChars * 100)]);
  };

  // ---- 書体: 指定スタックの中で実際に使える最初の書体（canvas 幅で判定）＋ document.fonts の読み込み済み ----
  const cvs = document.createElement('canvas').getContext('2d');
  const probe = 'あいう永ABCabc0123';
  const width = (f) => { cvs.font = `40px ${f}`; return cvs.measureText(probe).width; };
  const generic = ['serif', 'sans-serif', 'monospace', 'cursive', 'fantasy', 'system-ui'];
  const firstAvailable = (stack) => {
    const fams = stack.split(',').map(f => f.trim());
    for (const f of fams) {
      const bare = f.replace(/^["']|["']$/g, '');
      if (generic.includes(bare)) return bare + '（総称。実体は環境依存）';
      const q = `"${bare}"`;
      if (['monospace', 'serif', 'sans-serif'].some(g => width(`${q}, ${g}`) !== width(g))) return bare;
    }
    return '不明';
  };
  const stackCache = {};
  const usedFamily = (stack) => (stackCache[stack] ??= firstAvailable(stack));
  const loadedFaces = document.fonts ? [...document.fonts].filter(f => f.status === 'loaded').map(f => `${f.family} ${f.weight} ${f.style}`) : [];

  // ---- 本文サイズ・行間・ジャンプ率 ----
  const sizeDist = weighted(b => `${b.size}px`);
  const bodySize = sizeDist.length ? parseFloat(sizeDist[0][0]) : 16;
  const bodyBlocks = blocks.filter(b => b.size === bodySize);
  const bodyLH = bodyBlocks.length ? bodyBlocks.map(b => b.lh / b.size).sort((a, b) => a - b)[Math.floor(bodyBlocks.length / 2)] : 0;
  const maxSize = Math.max(...blocks.filter(b => b.chars >= 2).map(b => b.size), bodySize);
  const largest = blocks.filter(b => b.chars >= 2).sort((a, b) => b.size - a.size).slice(0, 5)
    .map(b => `${b.size}px w${b.s.fontWeight} 「${b.text.slice(0, 12)}」`);

  // ---- 揃え方: 文字の行が中央に置かれているか ----
  // text-align:center、または文字を包む要素（文字幅ちょうどの箱）が margin:auto / flex / grid で中央に寄せられている場合を数える。
  // 段組みの箱（max-width + margin:auto の main など）が中央にあるだけで、中の文字が左揃えなら数えない。
  const isCentered = (b) => {
    if (b.s.textAlign === 'center') return true;
    const tw = b.u.r - b.u.l;
    for (let p = b.el; p && p.parentElement && p !== document.body; p = p.parentElement) {
      const s = cs(p);
      const cw = p.getBoundingClientRect().width - px(s.paddingLeft) - px(s.paddingRight);
      if (!s.display.startsWith('inline') && cw > tw + 16) return false; // 文字より広い箱に達した: そこでの揃えは text-align で判定済み
      const par = p.parentElement; const ps = cs(par);
      if (ps.textAlign === 'center' && s.display.startsWith('inline')) return true;
      if (px(s.marginLeft) > 8 && Math.abs(px(s.marginLeft) - px(s.marginRight)) < 2) return true;
      if (ps.display.includes('flex')) {
        const row = !ps.flexDirection.startsWith('column');
        if (row && ps.justifyContent === 'center') return true;
        if (!row && (s.alignSelf === 'center' || ((s.alignSelf === 'auto' || s.alignSelf === 'normal') && ps.alignItems === 'center'))) return true;
      }
      if (ps.display.includes('grid') && (s.justifySelf === 'center' || ps.justifyItems === 'center')) return true;
    }
    return false;
  };
  let centeredChars = 0;
  const centeredSamples = [];
  blocks.forEach(b => {
    if (b.s.writingMode.startsWith('vertical')) return;
    if (isCentered(b)) { centeredChars += b.chars; if (centeredSamples.length < 6) centeredSamples.push(b.text.slice(0, 14)); }
  });

  // ---- 左端の揃い ----
  const edgeDist = weighted(b => b.s.writingMode.startsWith('vertical') ? null : `${Math.round(b.u.l / 4) * 4}px`);
  const top3Edge = round(edgeDist.slice(0, 3).reduce((a, x) => a + x[1], 0));

  // ---- ラベル（短い文字列）の言語と形 ----
  const formOf = (t) => {
    if (/^\d{1,3}([\s./\-—|・]|$)/.test(t)) return '番号';
    if (/^[A-Z0-9\s/&.\-—|・]+$/.test(t) && /[A-Z]/.test(t)) return '英大文字';
    if (/^[A-Za-z0-9\s/&.\-—|・']+$/.test(t) && /[a-z]/.test(t)) return '英小文字混じり';
    if (/[぀-ヿ一-鿿]/.test(t)) return '和文';
    return 'その他';
  };
  const inControl = (e) => !!e.closest('a,button,[role=button],nav,footer,table,dl');
  const labels = blocks.filter(b => b.chars <= 16 && b.size <= bodySize && !inControl(b.el) && !/[。、]/.test(b.text))
    .map(b => ({ text: b.text.slice(0, 16), form: formOf(b.text), ls: b.s.letterSpacing }));
  const labelForms = Object.entries(labels.reduce((m, l) => (m[l.form] = (m[l.form] || 0) + 1, m), {}));

  // ---- 角丸・影・グラデーション・カード ----
  const radius = (s) => Math.max(px(s.borderTopLeftRadius), px(s.borderTopRightRadius), px(s.borderBottomLeftRadius), px(s.borderBottomRightRadius));
  const radiusList = {}; let shadowCount = 0, gradientCount = 0, patternCount = 0;
  all.forEach(e => {
    const s = cs(e); const r = radius(s);
    if (r > 0) radiusList[`${r}px`] = (radiusList[`${r}px`] || 0) + 1;
    if (s.boxShadow !== 'none') shadowCount++;
    if (/gradient\(/.test(s.backgroundImage)) {
      // 小さい background-size で繰り返す gradient は格子・縞などの模様として分ける
      const sizes = s.backgroundSize.split(',').map(v => v.trim().split(/\s+/).map(px));
      const tiled = sizes.some(([w, h]) => w > 0 && w <= 64 && (h === undefined || (h > 0 && h <= 64))) && !/no-repeat/.test(s.backgroundRepeat);
      if (tiled) patternCount++; else gradientCount++;
    }
  });
  const solidBg = (s) => s.backgroundColor && !/rgba\(0, 0, 0, 0\)|transparent/.test(s.backgroundColor);
  const boxed = (s) => ['Top', 'Right', 'Bottom', 'Left'].every(k => px(s[`border${k}Width`]) > 0 && s[`border${k}Style`] !== 'none');
  const cards = all.filter(e => {
    if (e === document.body) return false;
    const r = e.getBoundingClientRect(); const s = cs(e);
    if (r.width < 120 || r.height < 60 || r.width > vw * 0.95) return false;
    const parentBg = e.parentElement ? cs(e.parentElement).backgroundColor : '';
    const look = s.boxShadow !== 'none' || boxed(s) || (solidBg(s) && s.backgroundColor !== parentBg && radius(s) > 0);
    return look && !!e.querySelector('[data-kansei-t]');
  });
  // 同じ親の下にカードが3つ以上並ぶ組を「カードの並び」と数える
  const byParent = new Map();
  cards.forEach(c => { const p = c.parentElement; if (p) byParent.set(p, (byParent.get(p) || 0) + 1); });
  const cardGrid = [...byParent.values()].filter(n => n >= 3).length;

  // ---- 固定・追従する要素 ----
  const pinned = all.filter(e => ['fixed', 'sticky'].includes(cs(e).position)).map(e => {
    const r = e.getBoundingClientRect();
    const kind = r.width >= vw * 0.5 && r.height < vh * 0.3 ? '横長（ヘッダー/バー型）' : 'ヘッダーではない（追従する小さい・縦長の要素）';
    return `${e.tagName.toLowerCase()} ${cs(e).position} ${Math.round(r.width)}x${Math.round(r.height)} ${kind}`;
  });

  // ---- ボタン・リンクの形 ----
  const controls = [...document.querySelectorAll('a,button,[role=button],input[type=submit],input[type=button]')].filter(visible);
  const buttonLike = controls.filter(e => { const s = cs(e); return solidBg(s) || boxed(s) || (px(s.paddingTop) >= 6 && px(s.paddingLeft) >= 10); });
  const buttonShapes = Object.entries(buttonLike.reduce((m, e) => { const s = cs(e); const r = e.getBoundingClientRect(); const rr = radius(s); const k = `${rr >= r.height / 2 - 1 ? 'pill' : rr > 0 ? `角丸${rr}px` : '角'}・${solidBg(s) ? '塗り' : boxed(s) ? '線' : '地のまま'}`; m[k] = (m[k] || 0) + 1; return m; }, {}));
  const underlinedLinks = controls.filter(e => e.tagName === 'A' && /underline/.test(cs(e).textDecorationLine)).length;

  // ---- 縦書き・数字の組み方 ----
  const vertical = blocks.filter(b => b.s.writingMode.startsWith('vertical')).map(b => `「${b.text.slice(0, 10)}」${b.size}px`);
  const numeric = blocks.filter(b => { const d = (b.text.match(/[0-9０-９:：,，.]/g) || []).length; return d >= 2 && d / b.chars >= 0.5; })
    .sort((a, b) => b.size - a.size);
  const tabular = all.filter(e => /tabular-nums/.test(cs(e).fontVariantNumeric)).length;

  // ---- セクション間余白: 内容（文字・画像）の縦の切れ目 ----
  const boxes = [...blocks.map(b => [docTop({ top: b.u.t }), docTop({ top: b.u.b })]),
    ...[...document.images, ...document.querySelectorAll('svg,video,canvas,hr')].filter(visible).map(e => { const r = e.getBoundingClientRect(); return [docTop(r), docTop({ top: r.bottom })]; })]
    .sort((a, b) => a[0] - b[0]);
  const merged = [];
  boxes.forEach(([t, b]) => { const m = merged[merged.length - 1]; if (m && t <= m[1] + 1) m[1] = Math.max(m[1], b); else merged.push([t, b]); });
  const gaps = merged.slice(1).map((m, i) => Math.round(m[0] - merged[i][1])).filter(g => g > 0);
  const sortedGaps = [...gaps].sort((a, b) => b - a);
  const median = (arr) => { const s = [...arr].sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : 0; };
  const bodyLinePx = bodySize * (bodyLH || 1.5);
  const bigGaps = sortedGaps.slice(0, 6);

  // ---- 地色候補（html と body を含む。面積比はスクリーンショットの画素で出す）----
  const rootBg = [document.documentElement, document.body].map(e => `${e.tagName.toLowerCase()}=${toHex(cs(e).backgroundColor)}${cs(e).backgroundImage !== 'none' ? ' +image' : ''}`);

  // ---- 絵文字・動き ----
  const allText = blocks.map(b => b.text).join(' ');
  const emoji = (allText.match(/\p{Extended_Pictographic}/gu) || []).length;
  const anims = document.getAnimations ? document.getAnimations().length : 0;
  const transitions = all.filter(e => { const t = cs(e).transitionDuration; return t && t.split(',').some(x => px(x) > 0); }).length;

  const textColors = weighted(b => toHex(b.s.color)).slice(0, 6);
  const fontsUsed = weighted(b => usedFamily(b.s.fontFamily)).slice(0, 5);
  const tokens = {
    'viewport': `${vw}x${vh} ページ高 ${pageHeight}px（画面 ${round(pageHeight / vh)} 枚分）`,
    '地色': `${rootBg.join(' / ')}（面積比はスクリーンショットの画素集計を見る）`,
    '文字色・差し色': `文字色 ${textColors.map(([c, p]) => `${c} ${p}%`).join(', ')}（差し色の面積比は画素集計）`,
    '書体': `${fontsUsed.map(([f, p]) => `${f} ${p}%`).join(', ')}${loadedFaces.length ? ` / 読み込み済み webfont: ${[...new Set(loadedFaces)].slice(0, 5).join('; ')}` : ' / webfont なし'}`,
    '本文サイズと行間': `${bodySize}px / 行間 ${round(bodyLH, 2)}（文字数で最多のサイズ）。字間 ${bodyBlocks[0] ? bodyBlocks[0].s.letterSpacing : '-'}`,
    'ジャンプ率': `${round(maxSize / bodySize, 2)}（最大 ${maxSize}px ÷ 本文 ${bodySize}px）。大きい順: ${largest.join(' | ')}`,
    'セクション間余白': `大きい切れ目 ${bigGaps.join(', ')}px = 本文行送りの ${bigGaps.map(g => round(g / bodyLinePx)).join(', ')} 倍。切れ目の中央値 ${median(gaps)}px`,
    '角丸・影': `角丸 ${Object.entries(radiusList).map(([k, v]) => `${k}×${v}`).join(', ') || 'なし'} / 影 ${shadowCount} / グラデーション ${gradientCount} / gradient で描いた繰り返し模様 ${patternCount}（どちらも目視で確かめる）`,
    '揃え方': `中央に置かれた文字 ${round(centeredChars / totalChars * 100)}%（例: ${centeredSamples.join(' | ') || 'なし'}）`,
    '左端の揃い': `左端位置（4px 丸め・文字数比）上位: ${edgeDist.slice(0, 4).map(([k, p]) => `${k} ${p}%`).join(', ')}。上位3つで ${top3Edge}%`,
    'ラベルの言語と形': `${labelForms.map(([k, v]) => `${k}×${v}`).join(', ') || 'なし'}（例: ${labels.slice(0, 6).map(l => `「${l.text}」`).join(' ')}）`,
    'カード': `カード状の箱 ${cards.length}（3つ以上並ぶ組 ${cardGrid}）`,
    '固定ヘッダー': pinned.some(p => p.includes('横長')) ? pinned.join(' / ') : `なし${pinned.length ? `（固定・追従する要素はある: ${pinned.join(' / ')}）` : ''}`,
    'ボタン・リンクの形': `ボタン状 ${buttonLike.length}${buttonShapes.length ? `（${buttonShapes.map(([k, v]) => `${k}×${v}`).join(', ')}）` : ''} / 下線リンク ${underlinedLinks}`,
    '縦書き': vertical.length ? vertical.join(', ') : 'なし',
    '数字の組み方': numeric.length ? `数字が主の文字列 ${numeric.length}。最大 ${numeric[0].size}px（本文の ${round(numeric[0].size / bodySize, 2)} 倍）「${numeric[0].text.slice(0, 12)}」 / tabular-nums ${tabular}` : '数字が主の文字列なし',
    '絵文字・動き': `絵文字 ${emoji} / アニメーション ${anims} / transition 指定 ${transitions}`,
  };
  return {
    tokens,
    raw: { bodySize, bodyLineHeight: round(bodyLH, 2), maxSize, textColors, fontsUsed, loadedFaces, sizeDist: sizeDist.slice(0, 8),
      centeredPercent: round(centeredChars / totalChars * 100), edgeDist: edgeDist.slice(0, 8), labels, cards: cards.length, cardGrid,
      pinned, buttonShapes, underlinedLinks, vertical, gaps: sortedGaps.slice(0, 12), bodyLinePx: round(bodyLinePx),
      radiusList, shadowCount, gradientCount, patternCount, emoji, rootBg, pageHeight, textBlocks: blocks.length },
  };
})()
