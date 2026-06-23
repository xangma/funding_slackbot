#!/usr/bin/env bash
set -u

umask 027

REPO=${FUNDING_SLACKBOT_REPO:-/home/xangma/repos/funding_slackbot}
ENV_FILE=${FUNDING_SLACKBOT_ENV_FILE:-/home/xangma/.config/funding_slackbot/env}
LOG=${FUNDING_SLACKBOT_LOG:-/var/log/funding_slackbot/funding-bot.log}

{
    printf '\n[%s] funding-bot start\n' "$(date -Is)"

    if [ ! -d "$REPO" ]; then
        printf '[%s] repo not found: %s\n' "$(date -Is)" "$REPO"
        exit 10
    fi
    if [ ! -r "$ENV_FILE" ]; then
        printf '[%s] env file not readable: %s\n' "$(date -Is)" "$ENV_FILE"
        exit 11
    fi
    if [ ! -x "$REPO/.venv/bin/funding-bot" ]; then
        printf '[%s] funding-bot executable not found: %s\n' "$(date -Is)" "$REPO/.venv/bin/funding-bot"
        exit 12
    fi

    cd "$REPO" || exit 10
    set -a
    # shellcheck source=/dev/null
    . "$ENV_FILE"
    set +a

    "$REPO/.venv/bin/funding-bot" --config config.yaml run
    rc=$?
    printf '[%s] funding-bot exit=%s\n' "$(date -Is)" "$rc"
    exit "$rc"
} >> "$LOG" 2>&1
