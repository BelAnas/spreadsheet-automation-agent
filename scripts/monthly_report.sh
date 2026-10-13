#!/usr/bin/env bash
# Build last month's report from the shared inbox folder. Meant for cron, for example at 06:00 on
# the second day of every month:
#
#   0 6 2 * * /opt/spreadsheet-automation-agent/scripts/monthly_report.sh >> /var/log/sales_report.log 2>&1
#
# Layout expected: INBOX/<YYYY-MM>/ holds that month's exports, REFERENCE/ the product list and targets.
# A Google Sheet can replace any file: put its link in TARGETS_LINK.
set -euo pipefail

cd "$(dirname "$0")/.."
INBOX="${INBOX:-data/inbox}"
REFERENCE="${REFERENCE:-data/reference}"
OUT="${OUT:-reports}"
PERIOD="${1:-$(date -d "$(date +%Y-%m-01) -1 month" +%Y-%m)}"

# shellcheck disable=SC2086
.venv/bin/python -m sheetagent run "$INBOX/$PERIOD" "$REFERENCE" ${TARGETS_LINK:-} --period "$PERIOD" --out "$OUT"
