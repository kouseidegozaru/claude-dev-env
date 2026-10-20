# Webページ・アプリ画面・スライド

## 観察のしかた

1. **ブラウザで実際に開く**（Claude のブラウザ画面か Chrome）。HTML を読むだけでは見え方が分からない
2. **スクリーンショットを撮る**：ファーストビュー、スクロール途中の代表的な2〜3か所、フッター。幅はPC（1440px前後）とスマホ（375px）の両方
3. **数値を取る**：下のスクリプトをページ上で実行し、色・文字・余白の実測値を得る。目測より正確で、「余白が広い」を「160px」に変えられる
4. **動きを見る**：スクロール、ホバー、読み込み時。スクリーンショットを時間をずらして2〜3枚撮るか、`getAnimations()` と transition の値を読む
5. 画像やスクリーンショットだけ渡された場合は 1〜2 を省き、画像から読み取れる範囲で推定値を書く（「推定」と明記）

### 実測用スクリプト

ブラウザの JavaScript 実行ツールでそのまま実行できます。結果は要約なので、気になる要素は個別に `getComputedStyle` で追加確認してください。

- `viewport` が `0x0` のときはブラウザ画面が非表示になっている。ウィンドウサイズを指定（例 1440×900）してから実行し直すと、幅に依存する値が正しく取れる
- `sectionPaddings` が 0 ばかりの場合、余白は内側の要素の margin や gap で作られている。主要なブロックの `getBoundingClientRect()` の top/bottom を並べ、ブロック間の距離を直接測る

```js
(() => {
  const els = [...document.querySelectorAll('body *')].filter(e => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden';
  });
  const count = (arr) => Object.entries(arr.reduce((m, k) => (m[k] = (m[k] || 0) + 1, m), {}))
    .sort((a, b) => b[1] - a[1]).slice(0, 12);
  const textEls = els.filter(e => [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()));
  const cs = (e) => getComputedStyle(e);
  // 背景色は面積で重み付けして、色の面積比を出す
  const bgArea = {};
  els.forEach(e => {
    const c = cs(e).backgroundColor;
    if (c && c !== 'rgba(0, 0, 0, 0)') {
      const r = e.getBoundingClientRect();
      bgArea[c] = (bgArea[c] || 0) + r.width * r.height;
    }
  });
  const totalBg = Object.values(bgArea).reduce((a, b) => a + b, 0) || 1;
  const h = [...document.querySelectorAll('h1,h2,h3,h4,p')].slice(0, 40).map(e => {
    const s = cs(e);
    return `${e.tagName} ${s.fontSize}/${s.lineHeight} w${s.fontWeight} ls${s.letterSpacing} ${s.color}`;
  });
  return {
    viewport: `${innerWidth}x${innerHeight}`, pageHeight: document.documentElement.scrollHeight,
    fontFamilies: count(textEls.map(e => cs(e).fontFamily)),
    fontSizes: count(textEls.map(e => cs(e).fontSize)),
    fontWeights: count(textEls.map(e => cs(e).fontWeight)),
    textColors: count(textEls.map(e => cs(e).color)),
    bgColorAreaPercent: Object.entries(bgArea).sort((a, b) => b[1] - a[1]).slice(0, 10)
      .map(([c, a]) => [c, Math.round(a / totalBg * 1000) / 10]),
    borderRadius: count(els.map(e => cs(e).borderRadius).filter(v => v !== '0px')),
    boxShadows: count(els.map(e => cs(e).boxShadow).filter(v => v !== 'none')),
    sectionPaddings: count([...document.querySelectorAll('section,header,footer,main > *')]
      .map(e => `${cs(e).paddingTop} / ${cs(e).paddingBottom}`)),
    contentMaxWidths: count(els.map(e => cs(e).maxWidth).filter(v => v !== 'none')),
    transitions: count(els.map(e => cs(e).transition).filter(v => v && v !== 'all' && !v.startsWith('all 0s'))),
    animations: document.getAnimations().slice(0, 10).map(a => ({
      name: a.animationName || a.transitionProperty || a.constructor.name,
      duration: a.effect?.getTiming().duration, easing: a.effect?.getTiming().easing })),
    headingSamples: h,
    imageCount: document.images.length,
  };
})()
```

## 要素一覧と、見るべき点

| 要素 | 観察すること | 見落としやすい点 |
|---|---|---|
| **フォント（タイポグラフィ）** | 書体（明朝／ゴシック／丸／欧文の種類）、本文と見出しのサイズ、**サイズの比（ジャンプ率）**、太さの数、行間、字間、1行の文字数、欧文と和文の組み合わせ | 字間（letter-spacing）と行間が印象を大きく決める。見出しの字間が広いと上品、詰めると力強い |
| **カラー** | 使っている色の一覧、**面積比**（例 背景 85%：文字 10%：アクセント 5%）、彩度と明度の傾向、純黒・純白を使っているか | 色そのものより面積比と彩度。高級感のあるサイトは彩度が低く、アクセントが極端に少ないことが多い |
| **配置（レイアウト）** | グリッド（列数・揃え）、本文の最大幅、**余白の量**（セクション間・要素間）、視線の流れ（Z型・F型・中央）、左右対称か非対称か | 余白は「空いている」のではなく設計されている。セクション間の余白は本文行間の何倍か、で記録する |
| **画像・図** | 写真／イラスト／3D／図の別、トーン（色温度・彩度・コントラスト）、切り抜き方、人物の有無と目線、画面に占める割合 | 全画像のトーンが揃っているか。揃っていると世界観が一つになる |
| **トンマナ（全体の性格）** | 第一印象の形容詞、角の丸さ・線の太さ・影の有無など**全要素に共通する性格** | 部品ごとの性格（丸ボタン＋鋭い書体など）が揃っているかどうか |
| **文言（コピー）** | 見出しの長さ、語尾（です・ます／体言止め／呼びかけ）、専門用語の量、数字の使い方、一人称・二人称 | 見出しの**短さ**と、本文との温度差。言葉の量そのものが余白にも影響する |
| **アニメーション・動き** | 何が・いつ動くか（読み込み・スクロール・ホバー）、速さ（ms）、イージング、動く距離 | 動きの「少なさ」。良いサイトは動く場所を絞り、速さとイージングを全体で統一している |
| **情報量と密度** | 1画面あたりの要素数、1セクションで伝えるメッセージ数、CTA（ボタン）の数 | AI は情報を詰め込みがち。参考作品の「1画面＝1メッセージ」などの密度を必ず記録する |
| **細部** | ボタン形状、リンクの下線、区切り線、アイコンの線の太さ、フォーカス表示 | 細部の一貫性。ここの不揃いが「安っぽさ」の原因になる |
| **レスポンシブ** | スマホでの文字サイズ・余白の縮み方、並びの変化 | PCの余白比をスマホでどの程度保っているか |

## 制作時の注意

- 参考サイトの値は**比率で写す**：「本文 16px、見出し 56px」→「見出しは本文の 3.5 倍」
- 実測値で CSS 変数（デザイントークン）を先に作ってから組み始めると、途中でブレにくい
- 完成後は参考と同じ幅でスクリーンショットを撮り、並べて見比べる（モード3）
