#!/usr/bin/env bash
# breakthrough の状態表示。表示するだけで、何も書かず、何も起動しない。
# 使い方: bash status.sh <RUN> [--all]   --all は応答済みの異議と退避した旧受領証の異議も原文で出す（最終報告用）
# 壊れたら: ls <RUN>/replies && cat <RUN>/replies/*.txt で同じ情報が読める。
RUN="${1:?使い方: status.sh <RUN> [--all]}"; ALL="${2:-}"
[ -d "$RUN" ] || { echo "RUN が見つからない: $RUN"; exit 1; }
REP="$RUN/replies"; JOBS="$RUN/jobs.txt"; CON="$RUN/concerns.md"
njob=0; nmiss=0; nrep=0; nfail=0; ndis=0; nopen=0; nodd=0

echo "## 受領証の無い担当（実行中・中断・無言の失敗のどれか）"
if [ -f "$JOBS" ]; then
  while read -r id _rest; do
    case "$id" in ''|\#*) continue ;; esac
    njob=$((njob+1))
    if [ ! -s "$REP/$id.txt" ]; then echo "- $id $_rest"; nmiss=$((nmiss+1)); fi
  done < "$JOBS"
else
  echo "- （jobs.txt がまだ無い）"
fi

echo "## 失敗・異議・書式崩れの受領証（原文のまま）"
for f in "$REP"/*.txt; do
  [ -e "$f" ] || continue
  nrep=$((nrep+1))
  id=$(basename "$f" .txt)
  raw=$(tr -d '\r' < "$f")
  first=$(printf '%s\n' "$raw" | grep -m1 -v '^[[:space:]]*$')
  tag=""
  # 失敗: 最初の欄が「失敗」
  if printf '%s' "$first" | grep -qE '^[[:space:]`*]*失敗'; then tag="$tag 失敗"; nfail=$((nfail+1)); fi
  # 異議: どこかに「異議」があり、「異議: なし」以外の異議の行がある（全角コロン・句点も許す）
  if printf '%s\n' "$raw" | grep '異議' | grep -vqE '異議[[:space:]]*[:：][[:space:]]*なし[[:space:]。.`*]*$'; then
    ndis=$((ndis+1))
    if [ -f "$CON" ] && grep -qE "^[[:space:]|*-]*$id([[:space:]:|]|$)" "$CON"; then tag="$tag 異議(応答済み)"
    else tag="$tag 異議(未応答)"; nopen=$((nopen+1)); fi
  fi
  # 書式崩れ: 「完了」「失敗」で始まらない、または「異議」の欄が無い
  if ! printf '%s' "$first" | grep -qE '^[[:space:]`*]*(完了|失敗)' || ! printf '%s' "$raw" | grep -q '異議'; then
    tag="$tag 書式崩れ"; nodd=$((nodd+1))
  fi
  show=""
  case "$tag" in *失敗*|*未応答*|*書式崩れ*) show=1 ;; *異議*) [ "$ALL" = "--all" ] && show=1 ;; esac
  [ -n "$show" ] && { echo "- [$id]$tag"; printf '%s\n' "$raw" | sed 's/^/    /'; }
done

if [ "$ALL" = "--all" ] && [ -d "$RUN/replies-old" ]; then
  echo "## 退避した旧受領証の異議（原文のまま）"
  for f in "$RUN/replies-old"/*.txt; do
    [ -e "$f" ] || continue
    if tr -d '\r' < "$f" | grep '異議' | grep -vqE '異議[[:space:]]*[:：][[:space:]]*なし[[:space:]。.`*]*$'; then
      echo "- [$(basename "$f" .txt)]"; tr -d '\r' < "$f" | sed 's/^/    /'
    fi
  done
fi

echo "## 件数"
echo "起動 $njob / 受領証 $nrep / 受領証なし $nmiss / 失敗 $nfail / 異議 $ndis（未応答 $nopen）/ 書式崩れ $nodd"
if [ "$njob" -gt 0 ] && [ $((njob - nmiss)) -ne "$nrep" ]; then
  echo "注意: 起動数と受領証の数が合わない（jobs.txt に無い受領証がある等）。cat $REP/*.txt で直接確かめること。"
fi
