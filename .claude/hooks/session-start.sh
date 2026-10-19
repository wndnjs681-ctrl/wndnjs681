#!/bin/bash
# Claude Code 원격(웹) 세션 시작 시 파이썬 의존성 설치
set -euo pipefail
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}"
pip install --quiet --disable-pip-version-check -r requirements.txt
