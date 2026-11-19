# BGM音作り（bgm-sound）

ゲームBGMなどのインストゥルメンタルの「音色」を担うスキル。MIDI を
実録サンプル／GM 音源でレンダリングし、ステムミックス・計測マスタリング・
納品ゲートまで定型手順で回す。音源台帳とギターの音作り（実録 DI＋アンプ
処理）もここに集約する。

作曲（仕様書と MIDI を作る工程）は姉妹スキル **bgm-compose** が担う。
このスキルは bgm-compose の成果物（仕様書と MIDI）を受け取って音にする。
「音が安っぽい」「基準曲と質感が合わない」問題はここ、「曲が悪い」問題は
bgm-compose、と切り分けて直す。

同梱物:

- `scripts/render.py` — FluidSynth 描画、EQ 補正、loudnorm 2パス、ループ化、納品ゲートまで一括実行
- `scripts/render_mix.py` — 主旋律チャンネルだけを自作ピアノ音源で描き、他パートを GM で描く混合レンダラー（ピアノ主導の曲用）
- `scripts/measure.py` — 音響計測（LUFS / トゥルーピーク / 低域比 / スペクトル重心 / 継ぎ目段差）
- `scripts/selfcheck.py` — 音色側の横断点検（道具・台帳の全音源の試し鳴らし・音色側スクリプトの構文）
- `scripts/render_sfz.py` — 汎用 SFZ レンダラー（SFZ 楽器とフォルダ楽器を鳴らす）
- `scripts/render_guitar.py` — 実録ギター DI サンプル専用レンダラー
- `scripts/dump_events.py` — ステム MIDI を音符イベント JSON に変換する
- `scripts/guitar_fx.py` — ギター音色プリセット集（歪み・モジュレーション・空間系）
- `references/instruments.md` — 音源台帳（実録サンプル楽器と SF2/GM 音源、ライセンス込み）
- `references/guitar.md` — ギター制作の詳細（素材検品・和音化・歪みの目安・ジャンル別レシピ・トラブル対応）
- `references/loudness.md` — ラウドネスとマスタリング（delivery プリセット・LRA/PLR/PSR・loudnorm の作法・マスターの仕事量予算）
- `references/eq-masking.md` — 周波数バランス・マスキング・EQ（7帯域・LTAS・処方順序・症状別処方・場面別ターゲット帯）
- `references/space.md` — 空間系（センドリターン・テンポ同期の減衰・送り量表・生成 IR・ディレイ）
- `references/samples.md` — 音源の表現力（台帳の表現スペック欄・足切り・render_sfz の制約・採用5ゲート・差し替え効果測定）
- `references/loop-fatigue.md` — ループ継ぎ目と長時間疲労（定常状態レンダリング・継ぎ目合議指標・疲労フラグ・10分テスト）
- `references/perceptual.md` — 知覚品質（ゲート2層化と距離レポート・安っぽさ5軸・試聴プロトコル・限界）
- `references/failure-recipes.md` — 失敗パターンと修正レシピ（音響版。症状→処方のトリアージと差し戻し判定）
- `references/environment.md` — 検証・環境再現性（環境フィンガープリント・golden 回帰・selfcheck 3分類・失敗検出）
- `assets/forge_piano.sfz` — 自作ピアノ音源の設計
- `instruments/forge_piano.sf2` — 自作ピアノ音源の本体（Salamander、CC BY 3.0。36MB のため claude-dev-env リポジトリには含めない）

## 前提（最初に確認する）

- `fluidsynth`（2.x）と `ffmpeg` が使えること。
- **サンプルレートは 44100 Hz に統一する**（render／measure／render_sfz／
  guitar_fx／IR／baseline が全て 44.1kHz 系のため。48kHz 化はしない。
  根拠は references/environment.md）。
- 空間（リバーブ）は FluidSynth 内蔵と afir センドの二重掛けを禁止し、
  使用経路・IR 種別・RT60・プリディレイ・送り量表を render_report に必ず
  記録する（references/space.md）。**記録が無い成果物は未完成として扱う。**
- 記譜確認（納品ゲートの一部）は mido の入った venv の Python で行う: `~/workspace/midi-bgm/venv/bin/python`
- 計測は numpy の入ったシステムの `python3` で行う（render.py が自動で使い分ける）。
- 音源は `references/instruments.md` の台帳にあるものだけを使う。既定は FluidR3_GM
  （`/usr/share/sounds/sf2/FluidR3_GM.sf2`）。
- 前提の確認は render.py が入口の事前検査で自動で行う（実行ファイル・venv の Python・
  音源ファイル・仕様書と MIDI の存在）。不足があれば、何が無いかと復旧手順
  （再導入コマンドや `--sf` の指定など）を表示して描画に入らず即座に止まる（終了コード 2）。
  環境の再構築直後などは、この表示どおりに復旧してから再実行する。
  また FluidSynth は音源を読み込めなくても無音の WAV を正常終了で出すことがあるため、
  render.py は描画直後に無音・ノイズフロアだけの出力を検出して失敗として報告する。
  手で先に確かめたい場合は `which fluidsynth ffmpeg ffprobe` と
  `ls -l /usr/share/sounds/sf2/FluidR3_GM.sf2` で足りる。
- 環境の移設・復旧直後と、スキル自体を編集した後は `python3 scripts/selfcheck.py`
  で音色側の横断点検をする（道具・台帳の全音源の試し鳴らし・音色側スクリプトの
  構文を一括確認。作曲側の点検は bgm-compose の selfcheck.py）。FAIL があれば
  直してからレンダリングに入る。

作業ファイルは曲ごとのフォルダに置く（例: `~/workspace/midi-bgm/<曲名>/`）。
フォルダには仕様書、生成 MIDI、render.py の出力（完成 OGG と render_report.json）を残す。

## 手順1: レンダリングとマスタリング

```
python3 scripts/render.py --spec spec.py --mid song.mid --out out --name song \
    --baseline <同場面の既存曲.ogg> [--sf <音源>] [--loop]
```

render.py が次を順に行い、`out/render_report.json` に全記録（段階ごとの所要時間つき）を残す:

0. 事前検査（前提の節参照）。不足があれば復旧手順を表示して即停止する。
1. FluidSynth で台帳の音源を使い WAV 描画（ピーク超過時はゲインを下げて描き直す）。
   描画直後に無音・ノイズフロアだけの出力を検出したら、後段の数値エラーにせず
   その場で失敗として報告して止まる。
2. 基準曲を計測して baseline.json を作る。
3. 低域比が基準の許容（±10pt）から外れていれば、lowshelf EQ（180Hz）を ±3.5dB ずつ
   「測定→補正→再測定」の反復（最大2回）で寄せる。補正は必ず生描画から当て直す。
4. 音量正規化の目標は**納品先プリセット**で決める（references/loudness.md。
   streaming は統合ラウドネス -14 LUFS／トゥルーピーク -1 dBTP 以下、
   game_asset は作業帯 -18〜-23 LUFS が初期値）。使ったプリセット名は
   render_report に必ず記録する。
   loudnorm は1パス目で測定し、測定値を使った2パス目の線形補正で当てる
   （パラメータ無しの loudnorm は使わない）。第2パスの normalization_type を
   回収し、linear 指定なのに dynamic だったら WARN/FAIL にする。符号化後の
   トゥルーピークが上限を僅かに超えることがあるため、その場合は loudnorm の
   TP 目標を下げて当て直す（render.py が自動で最大2回まで調整する）。
5. ループ指定時は末尾クロスフェード版も作り、継ぎ目を計測する（ループ処理の節参照）。
6. 納品ゲートと記譜確認を実行する（手順6参照。記譜確認の実体は bgm-compose の
   check_midi.py で、render.py が隣のスキルから自動で探して呼ぶ）。

### 手順1b（任意）: ステムミックス — 「音が安っぽい」と感じたら

一括描画は全楽器が無処理で中央に集まるため安っぽく聞こえやすい。チャンネルごとに
ステム描画し、パート別に処理してから混ぜると質感が大きく上がる。

- 歪みギター: asoftclip（tanh）で追い歪み＋キャビEQ（75 Hz 以下と 6.8 kHz 以上を切り、
  300 Hz を少し引く、3 kHz を少し上げる）。リズムギターは左右ダブルトラック
  （片側 11 ms 遅延＋薄いコーラスで別テイク感を出す）。
- ベース: 軽い飽和＋コンプ（acompressor の threshold は線形値。-20 dB = 0.1）。
- ドラム: コンプ＋高域シェルフでアタックを立てる。
- 空間: ノイズから生成した短い IR（afir）で各ステムに薄くリバーブを送る
  （-17〜-27 dB）。ドライ一辺倒が「安っぽさ」の主因のひとつ。
  空間の作り方の正本は references/space.md（センドリターン・送り量の
  ジャンル別表・生成 IR の仕様）、帯域の処方は references/eq-masking.md。
- 音源の差し替えも効く場面がある（台帳の GeneralUser GS はバンド編成で低域が厚い）。
  差し替えの効果測定は references/samples.md の手順（raw・同一条件・表現指標）で行う。

### 手順1c: 実録サンプル楽器

ギター以外の楽器も実録サンプルで鳴らせる（カリンバ、アコギ2種、ピアノ、マリンバ、
木琴、グロッケン、ハープ、弦セクション、フルート、チェロ、コントラバス、
ベース2種、バリトンサックス等。全て CC0/CC-BY で検品済み）。
対応表・出所・ライセンス・音域は `references/instruments.md` の台帳に集約する。

鳴らし方:

1. 曲のステム MIDI をチャンネルごとに分ける（曲フォルダの prep/produce スクリプト）。
2. ステム MIDI → 音符イベント JSON（`scripts/dump_events.py`）。
3. 楽器ごとに描く: SFZ 楽器とフォルダ楽器は `scripts/render_sfz.py`
   （SFZ の #include・default_path・ファイル名解決、フォルダ内の音名読み取り、
   ベロシティ層・ラウンドロビン、oneshot/sustain の2モードに対応）。
   SF2 楽器（ピアノ）は FluidSynth でステム MIDI を直接描く。
   エレキギター DI は手順2（`references/guitar.md`）。
4. ステムを手順1b のミックスに合流させる。

使い分けの目安:

- 主旋律: カリンバ、フルート、チェロ、ピアノ（forge）など実録の単音楽器が映える。
- 伴奏: アコギ（Emily / Shinyguitar）のアルペジオ、ハープのグリッサンド的ラン。
- 低域: マリンバ（柔らかい基礎）、コントラバス弓/ピチカート、エレキベース（バンド系）。
- 彩り: グロッケン（メロディのオクターブ重ね）、木琴、チューブラーベル（場面転換の一撃）。
- 面: バイオリンセクションのサステイン（パッド）、バリトンサックスの太いライン。

注意: VSCO 2 CE のマレット類はサンプリング間隔が疎い（5〜7半音）。移調で補完するため
近傍の音は実在感が高いが、大きく外れた音域は無理に使わない。SFZ には演奏ノイズの
region が混ざるものがあるため、render_sfz.py はパスに noise を含む region を自動で除外する。
render_sfz.py が解釈できる opcode は9種のみで、velocity crossfade・リリース
サンプル・keyswitch・CC 表現・ランダム RR は鳴らない。この制約と「鳴らない
前提の設計」、台帳の表現スペック欄と足切りは references/samples.md に集約する。
実証デモ: 9楽器の小品（`~/workspace/midi-bgm/instrument_garden/`）。

## 手順2: ギターの音作り

エレキギターは GM の歪みプリセットが最も電子音感が強い。実録 DI サンプル
（Karoryfer Black And Green Guitars、CC0）を自作レンダラーで鳴らし、アンプ歪み＋
キャビEQ を後段で掛ける方式を使う。大原則は「DI → アンプ → キャビネット」の
信号順序で、歪み処理は必ずオーバーサンプリングを挟む（倍率は歪みの硬さで
4倍／8倍を使い分ける。guitar.md 参照）。
詳細（素材の調達と検品、和音化、歪み量の目安、ジャンル別レシピ、トラブル対応）は
`references/guitar.md` に集約する。ギター中心の曲では必ず併せて読む。

## 手順3: ギター音色プリセット（guitar_fx.py）

ギターの音色は `scripts/guitar_fx.py` のプリセットで名前指定できる。DI 波形に対して
`python3 scripts/guitar_fx.py <preset> <in.wav> <out.wav> [--addon delay_375 ...]`。
ミックス内では PRESETS の "pre" チェーンを `[in]...[out]` で挟んで使う。
リバーブ系（surf/ambient/shoegaze）は生成 IR を使うため CLI 経由のみ。

- 素直系: clean / twang（明るい締まった音） / lofi（帯域を絞る＋ビブラート）
- 歪み系: crunch（軽い）/ overdrive（中域押し出し）/ blues（温かい軽歪み）/
  distortion（標準1段）/ lead_drive（2段・リード用）/ fuzz（ハードクリップ）/
  metal（中域スクープ）
- モジュレーション系: chorus_clean / flanger_clean / phaser_crunch / trem_drive
- 空間系: surf（スプリング強め）/ ambient（ディレイ＋ホール）/ shoegaze（ファズ＋ホール）
- 後段に足せる単体: chorus / flanger / phaser / tremolo / vibrato /
  delay_375 / delay_500 / slapback / presence / darken

歪み段はオーバーサンプリング後にクリップする（倍率は歪みの硬さで変える:
tanh かつ閾値 0.4 以上は 4 倍、閾値 0.34 以下・2段歪み・hard クリップは 8 倍。
素の asoftclip はエイリアシングでジャリつく）。プリセットの追加分
（lead_chord、前段ハイパス派生、ノイズゲート、キャビ3バリエーション、
bgm_backing、ナイロン用チェーン）と DI の基準レベルは references/guitar.md に
集約する。新しい音色が要る場合は既存チェーンの
boost/threshold/EQ を変えてプリセットとして追加し、この一覧にも足す。

## 手順4: 納品ゲート

完成判定は作曲者の自己チェックだけにしない。render.py が次を機械確認する:

- (a) LUFS が delivery プリセットの目標内（streaming は -14 ± 0.5。
  プリセットと LRA/PLR/PSR の判定は references/loudness.md）
- (b) トゥルーピークが -1 dBTP 以下
- (c) 帯域比が同場面基準の許容幅内（7帯域で判定。低域比（20–250 集約値）は
  ±15pt を踏襲。深刻度の段階は references/eq-masking.md）
- (d) 長さが仕様どおり（仕様の概算 −4秒〜+7秒。音源のリリース/リバーブの尾が付く分は上側に許す）
- 記譜: 主題の出現と戻り、音域（bgm-compose の check_midi.py を render.py が呼ぶ）

外れた項目は、音響の問題ならマスタリング（手順1）へ、記譜・編成の問題なら
作曲（bgm-compose）へ差し戻す。低域比のように編成で決まる項目は、EQ で
寄せきれない場合は bgm-compose 側の仕様（ベースの音域・密度・持続）に戻す。
ゲートの許容幅は仕様書の `gate` で曲ごとに上書きできる。
最後はユーザーの試聴で確定する。

ゲートは2層にする: 上のハードゲート（納品可能性）の上に、合否を付けない
**基準距離レポート**（各指標の基準値・実測値・差分。総合スコアは作らない）を
必須出力にする。症状が出たときの診断→処方は references/failure-recipes.md の
トリアージ順に進める。詳細は references/perceptual.md。

## 手順5: 音源の採用と台帳

使える音源は `references/instruments.md` の台帳に名前・ライセンス・出所を記録したものだけとする。
新規音源の採用は references/samples.md の**採用5ゲート**（ライセンス→公式取得→
波形＋音程検品→表現スペック棚卸し→実フレーズ試し鳴らし）を通す。要約すると:
(1) ライセンスと出所を確認して台帳に記録すること（有料販売する
ゲームに収録できる条件であること）、(2) 同じ MIDI を描いて計測し、表現指標
（ベロシティ応答・反復分散・LRA）が改善し帯域比・重心が悪化していないこと。
実録サンプル楽器の追加は「ライセンス確認→
**Release ZIP / 公式 tarball で取得**（git クローンはバイナリ破損の教訓あり、
guitar.md の検品節参照）→波形検品→試し鳴らし→台帳登録」の手順で増やせる。
GM 系 SF2 の役割は伴奏・下書き・穴埋めに限定し、主旋律は SFZ 実録側で描く
（samples.md）。

実測上の注意: 音源の差し替えだけでは低域比はほぼ動かない場面が多い
（ピアノ主体の場面で同一 MIDI 38.7% → 35.3%）。低域は bgm-compose の編成と
手順1の EQ で作り、音源差し替えは質感とダイナミクスの改善手段と位置づける。
ただしバンド編成では音源選びが低域にも大きく効く実測もある（FluidR3 16.4% → GeneralUser GS 36.4%）。

## ループ処理（音色側）

- 標準は**定常状態レンダリング**（3周描いて2周目を切り出す）で、継ぎ目は
  構造的に消す。末尾クロスフェードは定常状態が作れない場合の代替に格下げし、
  長さと カーブは固定でなく指標から決める探索型にする（固定2秒は廃止）。
- 継ぎ目の判定は3周連結波形に対する合議制（相対跳び比・スペクトルフラックス・
  帯域別段差・位相相関ほか）。納品前に10分テストを行う。疲労フラグ
  （high_freq／dynamic_stasis／short_cycle）と差し戻し先まで含め、
  詳細は references/loop-fatigue.md に集約する。
- ループの設計（頭と尾の進行・エネルギーを揃える）は bgm-compose 側の担当。

## 既知の限界

- 無料音源の音色の質感そのもの（1音の実在感や表情）は数値で測れず、天井が残る。
  帯域・音量・重心は測定と補正で基準に寄せられるが、「並べて遜色ない」の最終判定はユーザーの耳。
  天井を超えるには有料音源か生成AIとの併用が要る。
- 自作ピアノ（forge_piano）で改善できるのは主旋律ピアノの音色だけである。
- エレキギターは実録 DI＋自作レンダラー＋アンプ処理で GM を置き換えられる。
  ただしホロウボディの明るい音色が素材の個性として残る（guitar.md 参照）。
- 実録サンプルに無い楽器（金管の多く、ナイロンギター等）は GM のまま。
  ナイロンギターは候補（CC0・3層）が見つかっているが未採用で、SFZ 化と
  個別ライセンス確認を経てから台帳に載せる（references/samples.md に
  分離表記）。必要になったら手順5の手順で台帳を増やす。
- 特徴量の近さは音響的な近さであって曲の良さではない。主題・リズム・対比の
  問題は音側指標では検出できず、全チェック PASS でも却下は起こる
  （references/perceptual.md の「限界」参照）。
