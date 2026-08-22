#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_COMMAND="20 3 * * * ${PROJECT_ROOT}/scripts/server_backup.sh >> ${PROJECT_ROOT}/backups/backup.log 2>&1"
CURRENT="$(crontab -l 2>/dev/null || true)"
FILTERED="$(printf '%s\n' "${CURRENT}" | grep -v -F "${PROJECT_ROOT}/scripts/server_backup.sh" || true)"
{
  printf '%s\n' "${FILTERED}"
  printf '%s\n' "${BACKUP_COMMAND}"
} | sed '/^[[:space:]]*$/d' | crontab -
echo "Installed daily NF-Atlas backup at 03:20 for user $(id -un)."

