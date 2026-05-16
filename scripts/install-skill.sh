#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SKILL_NAME="remote-ssh"
TARGET_DIR="${HOME}/.claude/skills/${SKILL_NAME}"

echo "Installing ${SKILL_NAME} skill to ${TARGET_DIR} ..."

mkdir -p "${TARGET_DIR}"
cp "${REPO_ROOT}/skill/SKILL.md" "${TARGET_DIR}/SKILL.md"

echo "Done."
echo ""
echo "Skill installed at: ${TARGET_DIR}/SKILL.md"
echo ""
echo "To verify, restart Claude Code or run:"
echo "  ls ~/.claude/skills/${SKILL_NAME}/"
