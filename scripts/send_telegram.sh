#!/usr/bin/env bash
# 텔레그램으로 메시지를 보내는 스크립트.
#
# 사용법:
#   ./scripts/send_telegram.sh "보낼 메시지 내용"
#
# .env 파일(같은 프로젝트 루트)이 있으면 자동으로 읽어서
# TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 값을 가져온다.
# 클라우드 라우틴에서 실행할 때는 .env 대신 환경변수로
# 이 두 값이 이미 주입되어 있다고 가정한다.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]]; then
  echo "오류: TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 설정되지 않았습니다." >&2
  echo "  - 로컬 실행: .env 파일을 만들고 값을 채워주세요 (.env.example 참고)" >&2
  echo "  - 클라우드 라우틴 실행: 라우틴 설정 프롬프트에 값이 포함되어 있는지 확인하세요" >&2
  exit 1
fi

MESSAGE="${1:-}"
if [[ -z "$MESSAGE" ]]; then
  echo "오류: 보낼 메시지를 인자로 넘겨주세요." >&2
  echo '  예: ./scripts/send_telegram.sh "테스트 메시지"' >&2
  exit 1
fi

RESPONSE=$(curl -s -X POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d chat_id="${TELEGRAM_CHAT_ID}" \
  --data-urlencode text="${MESSAGE}" \
  -d parse_mode="Markdown")

OK=$(echo "$RESPONSE" | grep -o '"ok":[a-z]*' | head -1 | cut -d: -f2)

if [[ "$OK" == "true" ]]; then
  echo "텔레그램 전송 성공"
else
  echo "텔레그램 전송 실패. 응답:" >&2
  echo "$RESPONSE" >&2
  exit 1
fi
