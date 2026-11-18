# 音源台帳（instruments.md）

このスキルで使える音源の一覧。全てライセンス確認済み。
台帳に無い音源は、採用条件（末尾）を満たして台帳に追記するまで使わない。
最終更新: 2026-11-18（実録サンプル楽器は全点、破損検品＝波形ジャンプ数と再生テストを通過）。

## 第1部: 実録サンプル楽器

鳴らし方:

- **SFZ**: `python3 scripts/render_sfz.py --sfz <file> [--mode sustain] <events.json> <out.wav>`
- **フォルダ**: 同上 `--folder <dir>`（ファイル名中の音名をキーセンターとして読む）
- **SF2**: FluidSynth でステム MIDI を描く（render.py / render_mix.py）
- events.json は `scripts/dump_events.py` でステム MIDI から作る。

| 楽器 | 種別 | 出所 / ライセンス | 場所（導入済み環境） | 鳴らし方 | 音域の目安 | 備考 |
|---|---|---|---|---|---|---|
| ピアノ（forge_piano） | 鍵盤 | Salamander Grand Piano / CC BY 3.0（要クレジット表記） | スキル外に保持（36MB のためリポジトリに含めない）。設計は `assets/forge_piano.sfz` | SF2 | 全域 | 主旋律ピアノの本命。詳細は第2部の forge_piano 行 |
| カリンバ | 撥弦 | FreePats / CC0 | ~/workspace/soundfonts/freepats-kalimba/Kalimba-SFZ-20190723/Kalimba-20190723.sfz | SFZ (oneshot) | 48〜76 | ラウンドロビン付き。温かめの音 |
| アコギ（Emily Guitar） | 撥弦 | Karoryfer / CC0 | ~/workspace/soundfonts/emily/Emilyguitar/emily_basic.sfz | SFZ (oneshot) | 37〜86 | スチール弦。3層＋RR。chords/five_voice 等の別プログラムあり |
| アコギ（Shinyguitar） | 撥弦 | Karoryfer / CC0 | ~/workspace/soundfonts/sfz-extra/karoryfer.shinyguitar/Shinyguitar/Programs/acoustic.sfz | SFZ (oneshot) | 37〜84 | 明るめの音色。Emily と使い分け可 |
| エレキギター Black | 撥弦 | Karoryfer Black And Green / CC0 | ~/workspace/soundfonts/bng-release/Samples/black/ord | フォルダ（render_guitar.py） | 52〜98 | Hofner Club の DI。アンプ処理前提（guitar.md 参照） |
| エレキギター Green | 撥弦 | 同上 / CC0 | ~/workspace/soundfonts/bng-release/Samples/green/ord | フォルダ（render_guitar.py） | 52〜98 | Gretsch Anniversary の DI |
| マリンバ | 打楽器 | VSCO 2 CE / CC0 | ~/workspace/soundfonts/vsco2-src/Percussion/Marimba | フォルダ (oneshot) | 29〜84（疎） | 約5〜7半音間隔のサンプリング。移調で補完 |
| 木琴（Xylophone） | 打楽器 | VSCO 2 CE / CC0 | ~/workspace/soundfonts/vsco2-src/Percussion/Xylo | フォルダ (oneshot) | 55〜96（疎） | ff の遠録り1種 |
| グロッケン | 打楽器 | VSCO 2 CE / CC0 | ~/workspace/soundfonts/vsco2-src/Percussion/Glock | フォルダ (oneshot) | 67〜96（疎） | medium のみ |
| チューブラーベル | 打楽器 | VSCO 2 CE / CC0 | ~/workspace/soundfonts/vsco-tb/ | フォルダ (oneshot, tail 長め) | 60〜77（4音） | C4/C5/F5/G4 のみ。ピンポイント使用 |
| ハープ | 撥弦 | VSCO 2 CE / CC0 | ~/workspace/soundfonts/vsco2-src/Strings/Harp | フォルダ (oneshot) | 28〜101（やや疎） | mf 1層 |
| バイオリンセクション | 弦 | VSCO 2 CE / CC0 | ~/workspace/soundfonts/vsco2-src/Strings/Violin Section/susVib | フォルダ (sustain) | 43〜74 | サステイン（ビブラート）。パッド向き。Pizz/Spic/Trem も同リポジトリ内 |
| フルート | 管 | VSCO 2 CE / CC0 | ~/workspace/soundfonts/vsco2-src/Woodwinds/Flute/susNV | フォルダ (sustain) | 48〜84 | ノンビブラート。susvib/stac/expvib も同リポジトリ内 |
| チェロ | 弦 | Karoryfer × bigcat / CC0 | ~/workspace/soundfonts/sfz-extra/karoryfer-bigcat.cello/Karoryfer_Bigcat_cello/Programs/01- Bowed (velocity layer).sfz | SFZ (sustain) | 24〜72 | 弓弾き。03- Plucked.sfz でピチカートも可 |
| コントラバス | 弦 | DSmolken / CC0 | ~/workspace/soundfonts/sfz-extra/dsmolken.double-bass/dsmolken_double_bass/d_smolken_rubner_bass_arco.sfz | SFZ (sustain) | 24〜69 | 弓弾き。_pizz.sfz でピチカート |
| ベース（Meatbass） | 弦 | Karoryfer / CC0 | ~/workspace/soundfonts/sfz-extra/karoryfer.meatbass/Meatbass/Programs/04_pizz.sfz | SFZ (oneshot) | 21〜79 | コントラバスのピチカート主体。arco 系プログラムもあり |
| エレキベース（Pastabass） | 撥弦 | Karoryfer / CC0 | ~/workspace/soundfonts/sfz-extra/karoryfer.pastabass/Pastabass/combo_platter.sfz | SFZ (oneshot) | 40〜85 | プレシジョン系。バンドの低域に |
| バリトンサックス（Bear Sax） | 管 | Karoryfer / CC0 | ~/workspace/soundfonts/sfz-extra/karoryfer.bear-sax/Bear_Sax/Programs/2-solo-poly.sfz | SFZ (sustain) | 36〜68 | 太い単音管。デュオ版プログラムもあり |

取得元候補: sfzinstruments（GitHub org、Karoryfer 他、CC0 の SFZ が多数）、
FreePats（CC0 の SFZ/SF2）、VSCO 2 CE / VCSL（Versilian、CC0）。
VSCO 2 CE の未取得フォルダ（金管・木管の残り・鍵盤・ティンパニ等）は
~/workspace/soundfonts/vsco2-src の sparse checkout にフォルダを足せば増やせる。

## 第2部: SF2 / GM 音源

| 名前 | 形式 | ライセンス | 出所 | 場所 | 位置づけ |
|---|---|---|---|---|---|
| FluidR3_GM | SF2 | MIT | Debian パッケージ fluid-soundfont-gm | /usr/share/sounds/sf2/FluidR3_GM.sf2 | 既定の GM セット。全パートをまかなえる |
| MuseScore General | SF3 | MIT（一部 PD / CC0） | Debian パッケージ musescore-general-soundfont | /usr/share/sounds/sf3/MuseScore_General_Full.sf3（FluidSynth 2.x で描画可） | 代替 GM セット。質感とダイナミクスの改善手段 |
| GeneralUser GS 2.0.3 | SF2 | GeneralUser GS License v2.0（SF2 内蔵メタデータで確認: 自分の音楽制作に無制限に使用可、私用・商用とも。音源ファイル自体の再配布・販売は対象外） | S. Christian Collins 作。GitHub ミラー mrbumpy409/GeneralUser-GS から取得 | ~/workspace/soundfonts/GeneralUser-GS.sf2 | 代替 GM セット。楽器間のバランス調整が細かく、バンド系（ギター・ベース・ドラム）で低域の厚みが出る |
| Salamander Grand Piano | SFZ | CC BY 3.0（クレジット表記が必須） | Alexander Holm が録音・公開した Yamaha C5 のサンプルセット | 未導入 | ピアノ単体の任意強化。FluidSynth は SFZ 非対応のため、下記の forge_piano（SF2 化したもの）を使う |
| forge_piano | SF2 | CC BY 3.0（クレジット表記が必須: "Salamander Grand Piano by Alexander Holm, CC BY 3.0"） | Salamander の単音サンプルを検品し、音高×ベロシティ層で SFZ 設計して SF2 へ変換 | スキル外に保持（36MB）。設計の正は `assets/forge_piano.sfz` | ピアノ主導の曲の主旋律用。render_mix.py で主旋律チャンネルだけをこの音源で描き、他パートは GM で描く。ベロシティ層は音量だけでなく音色を変える。注意: このピアノ自体は低域を増やさない |

## 採用条件（新規音源）

次の2つを両方満たしたものだけを台帳に追記して使う。

1. ライセンスと出所を確認し、この台帳に記録すること（有料販売するゲームに収録できる条件であること）。
2. 同じ MIDI を現行セットと描き比べて計測し（measure.py）、帯域比・スペクトル重心が同場面の基準により近いこと。

実録サンプル楽器の追加は特に: **git クローンではなく Release ZIP / 公式 tarball で取得**
すること。git 経由の WAV はリポジトリのテキスト正規化（.gitattributes の text=auto）で
バイト単位に破損し、全点がノイズ化する実例がある（Karoryfer Black And Green Guitars、
実測: 重心約11kHz・波形の不連続が1音に数万回）。取得後は「1音の重心が 500〜1500 Hz
程度・大きな波形ジャンプが 0」で検品してから使う。

## 実測上の注意

- 音源の差し替えだけでは低域エネルギー比はほぼ動かない場面が多い（ピアノ主体の場面、
  同一 MIDI・同一ゲインの生描画で FluidR3_GM 38.7% → MuseScore General 35.3%、
  同場面の基準曲は 67.4%）。低域は編成（手順3）と EQ（手順4）で作るものとし、
  音源差し替えは質感とダイナミクスの改善手段と位置づける。
- 例外の実測（パンク曲の同一 MIDI・ゲイン 0.7 の生描画）: FluidR3_GM は低域 16.4%・
  重心 1832 Hz に対し GeneralUser GS は低域 36.4%・重心 1377 Hz と、バンド編成では
  音源差し替えでも低域比が大きく動いた。
- 描画ゲインは render.py の既定（1.0）でピークが -1 dBFS を超えたら自動で下げて
  描き直す。音源ごとに適正ゲインが違うため、初回は raw の計測値を確認する。
