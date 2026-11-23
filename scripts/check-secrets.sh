#!/usr/bin/env bash
# Fails if any file passed as an argument contains a likely secret or is a key file.
# Adapted from the auto-author check-secrets hook, but scans the given files so
# `pre-commit run --all-files` works too.
set -u

PATTERNS=(
    'AKIA[0-9A-Z]{16}'
    '(api[-_]?key|secret|token)[A-Za-z0-9_.-]*['\''"]?[[:space:]]*[=:][[:space:]]*['\''"][0-9a-zA-Z]{32,}['\'']?"?'
    'pass(word)[A-Za-z0-9_.-]*['\''"]?[[:space:]]*[=:][[:space:]]*['\''"][^'\''"]{8,}['\''"]'
    'gh[pousr]_[0-9a-zA-Z]{36}'
    'github_pat_[0-9a-zA-Z_]{82}'
    'xox[baprs]-[0-9a-zA-Z-]+'
    'eyJ[A-Za-z0-9_-]{100,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'
    '-----BEGIN [A-Z ]*PRIVATE KEY-----'
)

found=0
for file in "$@"; do
    case "$file" in
        scripts/check-secrets.sh | *.lock | package-lock.json) continue ;;
        *.pem | *.key)
            echo "ERROR: key file committed: $file"
            found=1
            continue
            ;;
    esac
    for pattern in "${PATTERNS[@]}"; do
        if grep -Eiq -- "$pattern" "$file" 2>/dev/null; then
            echo "ERROR: possible secret in $file (pattern: $pattern)"
            found=1
        fi
    done
done
exit "$found"
