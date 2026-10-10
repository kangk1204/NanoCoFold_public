#!/usr/bin/env bash
# (선택) 로컬 MSA 생성 DB 구축.
#
# 상업적 이용 안전을 위해 공용 ColabFold MSA 서버 대신 로컬 검색을 사용한다.
# 기본 경로: MMseqs2 정적 바이너리 + UniRef50 (8.8GB, FTP 속도 ~5-15MB/s)
#   - UniRef30 표준 DB(103GB)는 GWDG 서버 속도가 느려 선택적으로 사용.
# 실행 후 config 설정:
#   structure.msa.backend: mmseqs
#   structure.msa.mmseqs_binary: tools/mmseqs/bin/mmseqs
#   structure.msa.db: data/msa_db/uniref50_db
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/msa_db tools
DB_NAME="${1:-uniref50}"
case "$DB_NAME" in uniref50|uniref90|uniref100) ;; *) echo "지원 DB: uniref50, uniref90, uniref100" >&2; exit 2 ;; esac

# 1) 운영자가 고정 URL·SHA256을 확인한 MMseqs2 바이너리
if [ ! -x tools/mmseqs/bin/mmseqs ]; then
  archive_url="${NANOCOFOLD_MMSEQS_ARCHIVE_URL:-}"
  archive_sha="${NANOCOFOLD_MMSEQS_ARCHIVE_SHA256:-}"
  if [[ "$archive_url" != https://* || "$archive_url" == *"/latest/"* || ! "$archive_sha" =~ ^[0-9a-f]{64}$ ]]; then
    echo "검증한 tools/mmseqs/bin/mmseqs를 준비하거나 NANOCOFOLD_MMSEQS_ARCHIVE_URL(고정 HTTPS URL)과 NANOCOFOLD_MMSEQS_ARCHIVE_SHA256을 지정하세요." >&2
    exit 1
  fi
  archive_stage="$(mktemp -d tools/.mmseqs-install-XXXXXX)"
  curl -fL --proto '=https' --proto-redir '=https' "$archive_url" -o "$archive_stage/mmseqs.tar.gz"
  printf '%s  %s\n' "$archive_sha" "$archive_stage/mmseqs.tar.gz" | sha256sum -c -
  if tar tzf "$archive_stage/mmseqs.tar.gz" | awk 'BEGIN {bad=0} /^\// || /(^|\/)\.\.($|\/)/ || !/^mmseqs\// {bad=1} END {exit !bad}'; then
    echo "MMseqs2 아카이브에 예상하지 못한 경로가 있습니다. 임시 파일을 보존했습니다." >&2; exit 1
  fi
  tar xzf "$archive_stage/mmseqs.tar.gz" -C "$archive_stage"
  test -x "$archive_stage/mmseqs/bin/mmseqs"
  mv "$archive_stage/mmseqs" tools/mmseqs
fi
MM="$(pwd)/tools/mmseqs/bin/mmseqs"
"$MM" version

# 2) DB 다운로드 (기본 UniRef50)
DB_URL="https://ftp.uniprot.org/pub/databases/uniprot/uniref/${DB_NAME}/${DB_NAME}.fasta.gz"
FASTA="data/msa_db/${DB_NAME}.fasta.gz"
if [ -f "$FASTA" ]; then
  gzip -t "$FASTA" || { echo "기존 DB 파일이 손상됐습니다. 파일을 별도로 보존한 뒤 다시 받으세요: $FASTA" >&2; exit 1; }
else
  echo "[다운로드] $DB_URL (8-9GB, 시간 소요)"
  curl -fL --proto '=https' --proto-redir '=https' -C - -o "$FASTA.partial" "$DB_URL"
  gzip -t "$FASTA.partial"
  mv "$FASTA.partial" "$FASTA"
fi

# 3) MMseqs DB 생성 + 인덱스 (검색 가속)
DB_PREFIX="data/msa_db/${DB_NAME}_db"
if [ ! -f "${DB_PREFIX}.dbtype" ]; then
  echo "[createdb] $DB_PREFIX"
  "$MM" createdb "$FASTA" "$DB_PREFIX" -v 1
fi
if [ ! -f "${DB_PREFIX}.idx" ] && [ ! -d "${DB_PREFIX}_idx" ]; then
  if [ "${SKIP_INDEX:-0}" != "1" ]; then
    echo "[createindex] 검색 가속 (시간/디스크 추가 소요, SKIP_INDEX=1로 생략 가능)"
    "$MM" createindex "$DB_PREFIX" tmp_index -v 1 || echo "[경고] createindex 생략 (검색은 가능)"
  fi
fi

echo
echo "[완료] DB: $DB_PREFIX"
echo "config 예시:"
echo "  structure.msa:"
echo "    backend: mmseqs"
echo "    mmseqs_binary: tools/mmseqs/bin/mmseqs"
echo "    db: data/msa_db/${DB_NAME}_db"
echo "    threads: 8"
