#!/usr/bin/env bash
# Clawith Architecture Guard (scripts/arch-guard.sh)
# Automates P0 Constitution Checks for Clawith Agent operations.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VIOLATIONS=0

echo "🔍 Running Clawith Architecture Guard checks..."

# Helper function to report violation
report_violation() {
    local rule="$1"
    local msg="$2"
    local file="$3"
    echo "❌ VIOLATION [$rule] in $file: $msg"
    VIOLATIONS=$((VIOLATIONS + 1))
}

# -----------------------------------------------------------------------------
# RULE C1: Runtime Boundary Isolation
# backend/app/api/ must not directly import or invoke graph execution nodes
# -----------------------------------------------------------------------------
if [ -d "$ROOT_DIR/backend/app/api" ]; then
    while read -r line; do
        [ -z "$line" ] && continue
        file=$(echo "$line" | cut -d: -f1)
        report_violation "C1-RuntimeIsolation" "API layer must not directly import graph execution nodes" "$file"
    done < <(grep -rnE "from app\.services\.agent_runtime\.graph import|import graph_node|RuntimeNodeExecutor" "$ROOT_DIR/backend/app/api" 2>/dev/null || true)
fi

# -----------------------------------------------------------------------------
# RULE C4: Frontend HTTP Client Wrapper
# frontend/src/ must not directly import axios
# -----------------------------------------------------------------------------
if [ -d "$ROOT_DIR/frontend/src" ]; then
    while read -r line; do
        [ -z "$line" ] && continue
        file=$(echo "$line" | cut -d: -f1)
        report_violation "C4-NoDirectAxios" "Frontend code must use request wrapper instead of importing axios directly" "$file"
    done < <(grep -rnE "import axios from|import \* as axios" "$ROOT_DIR/frontend/src" 2>/dev/null || true)
fi

# -----------------------------------------------------------------------------
# RULE C2: Direct ORM Select in API/Services (Warn)
# backend/app/api/ and backend/app/services/ should converge DB calls to DAO
# -----------------------------------------------------------------------------
DIRECT_SELECT_COUNT=0
if [ -d "$ROOT_DIR/backend/app/api" ] || [ -d "$ROOT_DIR/backend/app/services" ]; then
    while read -r line; do
        [ -z "$line" ] && continue
        if [[ "$line" == *"# arch-guard: allow"* ]]; then
            continue
        fi
        file=$(echo "$line" | cut -d: -f1)
        DIRECT_SELECT_COUNT=$((DIRECT_SELECT_COUNT + 1))
    done < <(grep -rnE "select\(" "$ROOT_DIR/backend/app/api" "$ROOT_DIR/backend/app/services" 2>/dev/null || true)
    if [ "$DIRECT_SELECT_COUNT" -gt 0 ]; then
        echo "⚠️  WARNING [C2-DirectSelectInAPI] Found $DIRECT_SELECT_COUNT direct select(...) statement(s) in API/Service layers bypassing DAO"
    fi
fi

# -----------------------------------------------------------------------------
# RULE C5: Avoid Physical Foreign Keys in DB Models (Warn)
# -----------------------------------------------------------------------------
if [ -d "$ROOT_DIR/backend/app/models" ]; then
    while read -r line; do
        [ -z "$line" ] && continue
        file=$(echo "$line" | cut -d: -f1)
        echo "⚠️  WARNING [C5-NoPhysicalFK] Physical Foreign Key constraint found in $file (prefer application-level logical integrity)"
    done < <(grep -rnE "ForeignKey\(" "$ROOT_DIR/backend/app/models" 2>/dev/null || true)
fi

# -----------------------------------------------------------------------------
# RULE C6: Backend File Line Count Limit (Warn for files > 1000 lines)
# -----------------------------------------------------------------------------
LEGACY_OVERSIZED=0
if [ -d "$ROOT_DIR/backend/app" ]; then
    while read -r file; do
        [ -z "$file" ] && continue
        lines=$(wc -l < "$file" | tr -d ' ')
        if [ "$lines" -gt 1000 ]; then
            echo "⚠️  WARNING [C6-BackendLineLimit] $file exceeds 1000 lines limit ($lines lines)"
            LEGACY_OVERSIZED=$((LEGACY_OVERSIZED + 1))
        fi
    done < <(find "$ROOT_DIR/backend/app" -type f -name "*.py" 2>/dev/null || true)
fi

# -----------------------------------------------------------------------------
# RULE: Frontend File Line Count Limit (Warn for legacy files > 600 lines)
# -----------------------------------------------------------------------------
if [ -d "$ROOT_DIR/frontend/src" ]; then
    while read -r file; do
        [ -z "$file" ] && continue
        lines=$(wc -l < "$file" | tr -d ' ')
        if [ "$lines" -gt 600 ]; then
            echo "⚠️  WARNING [Style-LineLimit] $file exceeds 600 lines limit ($lines lines)"
            LEGACY_OVERSIZED=$((LEGACY_OVERSIZED + 1))
        fi
    done < <(find "$ROOT_DIR/frontend/src" -type f \( -name "*.ts" -o -name "*.tsx" \) 2>/dev/null || true)
fi

# -----------------------------------------------------------------------------
# Final Verdict
# -----------------------------------------------------------------------------
echo ""
if [ "$LEGACY_OVERSIZED" -gt 0 ]; then
    echo "ℹ️  Found $LEGACY_OVERSIZED legacy frontend file(s) exceeding 600 lines (Warnings)."
fi

if [ "$VIOLATIONS" -gt 0 ]; then
    echo "🚨 Arch-Guard failed with $VIOLATIONS P0 violation(s). Please fix before committing."
    exit 1
else
    echo "✅ Arch-Guard passed! All P0 constitution checks clean."
    exit 0
fi

