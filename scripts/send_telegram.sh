#!/usr/bin/env bash
# 텔레그램으로 메시지를 보내는 스크립트.
#
# 사용법 (둘 다 지원):
#   ./scripts/send_telegram.sh "보낼 메시지 내용"        # 인자로 전달
#   ./scripts/send_telegram.sh < message.txt              # 파일/표준입력으로 전달
#   echo "메시지" | ./scripts/send_telegram.sh
#
# 한글처럼 멀티바이트 텍스트는 셸 인자로 넘기면 일부 환경(특히 Windows
# 콘솔)에서 인코딩이 깨질 수 있다. 안전하게 보내려면 파일/stdin 방식을 쓴다.
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

TMP_MSG_FILE=""
cleanup() { [[ -n "$TMP_MSG_FILE" ]] && rm -f "$TMP_MSG_FILE"; }
trap cleanup EXIT

if [[ -n "${1:-}" ]]; then
  # 인자로 메시지가 왔으면(짧은 영문 텍스트 등) 그대로 파일에 담아 재사용한다.
  TMP_MSG_FILE="$(mktemp)"
  printf '%s' "$1" > "$TMP_MSG_FILE"
elif [[ ! -t 0 ]]; then
  # 표준입력(stdin)으로 메시지가 들어온 경우 — 한글 등 멀티바이트에 안전.
  TMP_MSG_FILE="$(mktemp)"
  cat > "$TMP_MSG_FILE"
else
  echo "오류: 보낼 메시지를 인자로 넘기거나 표준입력으로 전달해주세요." >&2
  echo '  예1: ./scripts/send_telegram.sh "테스트 메시지"' >&2
  echo '  예2: ./scripts/send_telegram.sh < message.txt' >&2
  exit 1
fi

if [[ ! -s "$TMP_MSG_FILE" ]]; then
  echo "오류: 보낼 메시지 내용이 비어 있습니다." >&2
  exit 1
fi

# Windows용 mingw curl.exe는 POSIX 경로(/tmp/... 등)를 "@파일" 인자로 못 읽는
# 경우가 있다. cygpath가 있으면(Git Bash/MSYS 환경) Windows 경로로 변환해준다.
# 클라우드(Linux) 환경에는 cygpath가 없으므로 원래 경로를 그대로 쓴다.
CURL_MSG_PATH="$TMP_MSG_FILE"
if command -v cygpath >/dev/null 2>&1; then
  CURL_MSG_PATH="$(cygpath -w "$TMP_MSG_FILE")"
fi

RESPONSE=$(curl -s -X POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d chat_id="${TELEGRAM_CHAT_ID}" \
  --data-urlencode text@"${CURL_MSG_PATH}" \
  -d parse_mode="Markdown")

OK=$(echo "$RESPONSE" | grep -o '"ok":[a-z]*' | head -1 | cut -d: -f2)

if [[ "$OK" == "true" ]]; then
  echo "텔레그램 전송 성공"
else
  echo "텔레그램 전송 실패. 응답:" >&2
  echo "$RESPONSE" >&2
  exit 1
fi
