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
```

## 使う側での導入

```bash
git subtree add --prefix=.claude https://github.com/kouseidegozaru/claude-dev-env main --squash
```

これだけで `.claude/settings.json` `.claude/CLAUDE.md` `.claude/skills/` が
所定の位置に入り、追加設定なしで有効になる。

更新:

```bash
git subtree pull --prefix=.claude https://github.com/kouseidegozaru/claude-dev-env main --squash
```

### 前提

- **`.claude/` が既に存在するリポジトリでは `subtree add` が失敗する。** 先に退避するか、
  中身を手動でマージする。
- **`git subtree` が使えない git がある。** このリポジトリの Codespace では PATH 上の
  git 2.53.0 (`/usr/local/bin/git`) に `git-subtree` が入っておらず、
  `/usr/bin/git` 2.43.0 にはある。使えない場合:

  ```bash
  GIT_EXEC_PATH=/usr/lib/git-core git subtree add --prefix=.claude https://github.com/kouseidegozaru/claude-dev-env main --squash
  ```

- 初回はプロジェクト設定のフックに対する信頼確認が出る。承認するとフックが有効になる。

## rtk バイナリについて

`bin/rtk` は git に直接コミットしている（10MB）。**Git LFS は使っていない。**
`git subtree` は内部で `git fetch` しか行わず、LFS の実体は fetch では転送されないため、
使う側で smudge エラーになりファイルが展開されない。加えて使う側が `.gitattributes` を
継承し、git-lfs 必須・自前の LFS ストレージ消費が伝染する。

バイナリは static-pie（musl 静的リンク）で glibc 非依存。**x86_64 Linux 専用**。
macOS / arm64 では動かないので、その場合は各自 `~/.local/bin` に入れて
`bin/rtk-hook.sh` の `RTK` を差し替える。

## このリポジトリ自身では設定が効かない

ルートが `.claude` の中身であるという構造上、このリポジトリを直接開いても
`settings.json` や `CLAUDE.md` は読まれない（Claude Code が見るのは `.claude/` 配下）。
挙動を確認したいときは、別のリポジトリに subtree して試す。

## 同梱物

| 名前 | 出所 | ライセンス |
|---|---|---|
| rtk (Rust Token Killer) | [rtk-ai/rtk](https://github.com/rtk-ai/rtk) v0.46.0 | Apache-2.0 |
| genshijin 原始人 | [InterfaceX-co-jp/genshijin](https://github.com/InterfaceX-co-jp/genshijin) v1.5.0 | MIT |
| grill-me | [RobMitt/grill-me-skill](https://github.com/RobMitt/grill-me-skill) | ライセンス表記なし |
