#!/bin/bash
# End-to-end test runner for multi-tool benchmark suite
# Runs isolated tests comparing LineageLens with competing tools
# All results stored in /tmp/scratch_* directories (safe to delete after testing)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTS_DIR="$SCRIPT_DIR/scratch"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
log_header() {
    echo ""
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}"
}

log_test() {
    echo -e "${YELLOW}→ $1${NC}"
}

log_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

log_error() {
    echo -e "${RED}✗ $1${NC}"
}

check_tool() {
    if ! command -v "$1" &> /dev/null; then
        return 1
    fi
    return 0
}

# Check prerequisites
log_header "Checking Prerequisites"

log_test "Claude CLI"
if check_tool "claude"; then
    log_success "Claude CLI found"
    claude --version
else
    log_error "Claude CLI not found. Install: npm i -g @anthropic-ai/claude"
    exit 1
fi

log_test "GitHub CLI"
if check_tool "gh"; then
    log_success "GitHub CLI found"
    gh --version
else
    log_error "GitHub CLI not found. Install: brew install gh (macOS) or see https://cli.github.com"
    exit 1
fi

log_test "LineageLens"
if check_tool "lineagelens"; then
    log_success "LineageLens found"
    lineagelens --version
else
    log_error "LineageLens not found. Install: pip install lineagelens"
    exit 1
fi

log_test "CodeGraph (optional)"
if check_tool "codegraph"; then
    log_success "CodeGraph found"
    codegraph --version
    CODEGRAPH_AVAILABLE=1
else
    log_error "CodeGraph not found (optional). To use: npm i -g @colbymchenry/codegraph"
    CODEGRAPH_AVAILABLE=0
fi

log_test "Graphify (optional)"
if check_tool "graphify"; then
    log_success "Graphify found"
    graphify --version
    GRAPHIFY_AVAILABLE=1
else
    log_error "Graphify not found (optional). To use: uv tool install graphifyy"
    GRAPHIFY_AVAILABLE=0
fi

# Test 1: LineageLens vs Baseline
log_header "Test 1: LineageLens vs Baseline"
log_test "Configuration: $TESTS_DIR/config_e2e_lineagelens_vs_baseline.yaml"

if [ -f "$TESTS_DIR/config_e2e_lineagelens_vs_baseline.yaml" ]; then
    python "$SCRIPT_DIR/run_pr_benchmark.py" --config "$TESTS_DIR/config_e2e_lineagelens_vs_baseline.yaml"
    if [ $? -eq 0 ]; then
        log_success "Test 1 complete: LineageLens vs Baseline"
        echo "Results at: /tmp/scratch_e2e_lineagelens_baseline/results/"
    else
        log_error "Test 1 failed"
        exit 1
    fi
else
    log_error "Config file not found: $TESTS_DIR/config_e2e_lineagelens_vs_baseline.yaml"
    exit 1
fi

# Test 2: All four tools (if optional tools available)
if [ $CODEGRAPH_AVAILABLE -eq 1 ] || [ $GRAPHIFY_AVAILABLE -eq 1 ]; then
    log_header "Test 2: All Available Tools"
    log_test "Configuration: $TESTS_DIR/config_e2e_all_four.yaml"

    if [ -f "$TESTS_DIR/config_e2e_all_four.yaml" ]; then
        python "$SCRIPT_DIR/run_pr_benchmark.py" --config "$TESTS_DIR/config_e2e_all_four.yaml"
        if [ $? -eq 0 ]; then
            log_success "Test 2 complete: All tools"
            echo "Results at: /tmp/scratch_e2e_all_four/results/"
        else
            log_error "Test 2 failed (some tools may be missing)"
        fi
    else
        log_error "Config file not found: $TESTS_DIR/config_e2e_all_four.yaml"
    fi
else
    log_header "Test 2: Skipped"
    log_test "CodeGraph or Graphify required for full test"
    echo "Install CodeGraph: npm i -g @colbymchenry/codegraph"
    echo "Install Graphify: uv tool install graphifyy"
fi

# Final summary
log_header "E2E Tests Complete"
log_success "All completed tests passed"
echo ""
echo "Results stored in /tmp/scratch_* directories"
echo "Safe to delete: rm -rf /tmp/scratch_e2e_*"
echo ""
