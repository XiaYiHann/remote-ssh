#!/usr/bin/env bash
set -euo pipefail

REPO_NAME="remote-ssh"
REPO_URL="https://github.com/yourusername/remote-ssh.git"

# Detect whether this script is being run from inside the repo or via curl/pipe.
SCRIPT_SOURCE="${BASH_SOURCE[0]}"
SCRIPT_DIR=""
if [ -f "${SCRIPT_SOURCE}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${SCRIPT_SOURCE}")" && pwd)"
fi

# If skill/SKILL.md is missing, we are not inside the cloned repo — fetch it first.
if [ -z "${SCRIPT_DIR}" ] || [ ! -f "${SCRIPT_DIR}/skill/SKILL.md" ] || [ ! -f "${SCRIPT_DIR}/pyproject.toml" ]; then
    TMP_DIR="$(mktemp -d)"
    echo "==> Fetching ${REPO_NAME} repository ..."
    git clone --depth 1 "${REPO_URL}" "${TMP_DIR}/${REPO_NAME}"
    SCRIPT_DIR="${TMP_DIR}/${REPO_NAME}"
    echo "    Cloned to ${SCRIPT_DIR}"
fi

SKILL_SRC="${SCRIPT_DIR}/skill/SKILL.md"
SKILL_DST_DIR="${HOME}/.agents/skills/${REPO_NAME}"
CLAUDE_SKILLS_PARENT="${HOME}/.claude/skills"
CLAUDE_SKILL_DIR="${CLAUDE_SKILLS_PARENT}/${REPO_NAME}"

echo "==> Installing ${REPO_NAME} CLI ..."
cd "${SCRIPT_DIR}"
pip install -e . -q
echo "    CLI installed: remote-ssh"

echo "==> Installing ${REPO_NAME} skill ..."

# If the agents skill path is a broken or self-referencing symlink, remove it.
if [ -L "${SKILL_DST_DIR}" ]; then
    if [ ! -e "${SKILL_DST_DIR}" ]; then
        echo "    Removing broken symlink: ${SKILL_DST_DIR}"
        rm -f "${SKILL_DST_DIR}"
    fi
fi

mkdir -p "${SKILL_DST_DIR}"
cp "${SKILL_SRC}" "${SKILL_DST_DIR}/SKILL.md"
echo "    Skill copied to: ${SKILL_DST_DIR}/SKILL.md"

# Check whether .claude/skills is already a symlink to .agents/skills.
# If so, no extra symlink is needed — the skill is already visible to Claude.
if [ -L "${CLAUDE_SKILLS_PARENT}" ] && [ "$(readlink -f "${CLAUDE_SKILLS_PARENT}" 2>/dev/null || readlink "${CLAUDE_SKILLS_PARENT}")" = "${HOME}/.agents/skills" ]; then
    echo "    ~/.claude/skills already points to ~/.agents/skills — no extra symlink needed"
else
    # Clean up the Claude skill path and create a fresh symlink.
    if [ -e "${CLAUDE_SKILL_DIR}" ] || [ -L "${CLAUDE_SKILL_DIR}" ]; then
        echo "    Removing old Claude skill path: ${CLAUDE_SKILL_DIR}"
        rm -rf "${CLAUDE_SKILL_DIR}"
    fi
    ln -s "${SKILL_DST_DIR}" "${CLAUDE_SKILL_DIR}"
    echo "    Symlinked: ${CLAUDE_SKILL_DIR} -> ${SKILL_DST_DIR}"
fi

echo ""
echo "Done. To verify:"
echo "  remote-ssh --help"
echo "  ls -l ~/.claude/skills/${REPO_NAME}"
