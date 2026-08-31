# claude-dev-env

Claude Code の共通開発環境。**このリポジトリのルートが、そのまま使う側の `.claude/` の中身になる。**

```
settings.json            rtk PreToolUse フック
CLAUDE.md                genshijin デフォルト方針・作業ルール（常時ロード）
bin/rtk                  Rust Token Killer 本体（10MB, x86_64 Linux 専用）
bin/rtk-hook.sh          フックのシム
skills/genshijin/        原始人モード（超圧縮コミュニケーション）
skills/grill-me/         計画・設計の詰問
licenses/                同梱物のライセンス
README.md                このファイル。配布物には含まれない（export-ignore）
.gitattributes           配布対象の指定。同上
```

## 使う側での導入

**このリポジトリのルート全体が `.claude/` の中身になる。** リポジトリ内に `.claude`
サブディレクトリがあるわけではないので、展開元はルートそのものになる。

使う側のリポジトリのルートで:

```bash
git clone --depth=1 https://github.com/kouseidegozaru/claude-dev-env /tmp/claude-dev-env-src
mkdir -p .claude
git -C /tmp/claude-dev-env-src archive HEAD | tar -x -C .claude
rm -rf /tmp/claude-dev-env-src
git add .claude
git commit -m "Add .claude config from claude-dev-env"
```

これで `.claude/settings.json` `.claude/CLAUDE.md` `.claude/skills/` が所定の位置に入り、
追加設定なしで有効になる。更新も同じ手順（同じコマンドを再実行すれば上書きされる）。
履歴は追跡されないので、使う側で手を入れている場合は差分を手動でマージする。

`cp -r` ではなく `git archive | tar -x` を使う理由:

- `.gitattributes` の `export-ignore`（README 等）が効くのは `git archive` だけ
- `.git` が混入しない（tracked file のみ展開される）
- `bin/rtk` の実行ビットが保たれる（`chmod +x` 不要）
- `.claude/` が既に存在しても中身が展開される。`cp -r src dest` のように
  `dest/src` へネストしない

### 前提

- Claude Code をインストール済み
  ```bash
  curl -fsSL https://claude.ai/install.sh | bash
  ```

- 初回はプロジェクト設定のフックに対する信頼確認が出る。承認するとフックが有効になる。

### 代替: git subtree

履歴ごと追跡したい場合は subtree も使える。ただし制約が多い。

```bash
git subtree add --prefix=.claude https://github.com/kouseidegozaru/claude-dev-env main --squash
git subtree pull --prefix=.claude https://github.com/kouseidegozaru/claude-dev-env main --squash
```

- **作業ツリーに未コミット変更があると `subtree add` が拒否される**
  （`fatal: working tree has modifications. Cannot add.`）。先にコミットか stash。
- **`.claude/` が既に存在するリポジトリでは `subtree add` が失敗する。**
- **`git subtree` が入っていない git がある。** このリポジトリの Codespace では PATH 上の
  git 2.53.0 (`/usr/local/bin/git`) に `git-subtree` が無く、`/usr/bin/git` 2.43.0 にはある。
  その場合:

  ```bash
  GIT_EXEC_PATH=/usr/lib/git-core git subtree add --prefix=.claude https://github.com/kouseidegozaru/claude-dev-env main --squash
  ```

## rtk バイナリについて

`bin/rtk` は git に直接コミットしている（10MB）。**Git LFS は使っていない。**
LFS にすると使う側に git-lfs 必須・自前の LFS ストレージ消費が伝染する。
特に `git subtree` は内部で `git fetch` しか行わず、LFS の実体は fetch では転送されないため、
使う側で smudge エラーになりファイルが展開されない。

バイナリは static-pie（musl 静的リンク）で glibc 非依存。**x86_64 Linux 専用**。
macOS / arm64 では動かないので、その場合は各自 `~/.local/bin` に入れて
`bin/rtk-hook.sh` の `RTK` を差し替える。

## このリポジトリ自身では設定が効かない

ルートが `.claude` の中身であるという構造上、このリポジトリを直接開いても
`settings.json` や `CLAUDE.md` は読まれない（Claude Code が見るのは `.claude/` 配下）。
挙動を確認したいときは、別のリポジトリの `.claude/` に入れて試す。

## 同梱物

| 名前 | 出所 | ライセンス |
|---|---|---|
| rtk (Rust Token Killer) | [rtk-ai/rtk](https://github.com/rtk-ai/rtk) v0.46.0 | Apache-2.0 |
| genshijin 原始人 | [InterfaceX-co-jp/genshijin](https://github.com/InterfaceX-co-jp/genshijin) v1.5.0 | MIT |
| grill-me | [RobMitt/grill-me-skill](https://github.com/RobMitt/grill-me-skill) | ライセンス表記なし |
