#!/bin/bash
# 下载并解压飞书 bug 日志附件
# 用法: fetch_bug_log.sh <bug_id> <download_url> <sign>

BUG_ID="$1"
URL="$2"
SIGN="$3"
WORKDIR="/tmp/bug_${BUG_ID}"

rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR"
curl -s -o log.zip -H "X-Meego-File-Sign: ${SIGN}" "${URL}"
unzip -o log.zip
cat log/current_log.info
echo "=== DONE ==="
ls log/current_log_dir/
