# Bash completion for the unified `ger` CLI (ger push, ger log, …).
# shellcheck disable=SC2207,SC2154
#
# Install: source this file from ~/.bashrc, or see docu/Completion.md
#
# Optional: Git's bash completion for __git_complete_refs on revision arguments.

__gwt_flags() {
    local cur=$1
    shift
    COMPREPLY=( $(compgen -W "$*" -- "$cur") )
}

_ger_push() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" \
            --help \
            --dry-run \
            -y --yes \
            -i \
            --show-attributes --no-show-attributes \
            --all \
            --target \
            --save-target \
            --force-boundary \
            --no-config-patterns \
            --ignore-pattern \
            --reviewers \
            -v --verbose --debug-log
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_log() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" \
            --help \
            --full \
            --oneline --no-oneline \
            --json \
            --color \
            --url --show-url \
            --show-change-id \
            -v --verbose --debug-log
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_branch() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [ "${COMP_CWORD:-0}" -eq 2 ]; then
        __gwt_flags "$cur" show init set-target set-reviewers
        return
    fi
    local sub="${COMP_WORDS[2]}"
    if [[ "$cur" == -* ]]; then
        case "$sub" in
            init)
                __gwt_flags "$cur" --help --target --reviewers -v --verbose --debug-log
                ;;
            show|set-target|set-reviewers)
                __gwt_flags "$cur" --help -v --verbose --debug-log
                ;;
            *)
                __gwt_flags "$cur" --help -v --verbose --debug-log
                ;;
        esac
        return
    fi
}

_ger_edit() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" --help --reword --drop -v --verbose --debug-log
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_cid() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" --help --check-duplicates --start-at-remote -v --verbose --debug-log
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_sha() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" \
            --help \
            --range \
            --all \
            --short \
            --subject \
            --json \
            -v --verbose --debug-log
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_show() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" \
            --help \
            --full \
            --comment-tail-lines \
            --json \
            --color \
            -v --verbose --debug-log
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_comments() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" \
            --help \
            --whole-chain \
            --no-skip-fixups \
            --all \
            --open \
            --json \
            --full \
            --oneline \
            -v --verbose --debug-log
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [ "${COMP_CWORD:-0}" -eq 1 ]; then
        __gwt_flags "$cur" branch comments cid edit log push sha show
        return
    fi
    local sub="${COMP_WORDS[1]}"
    case "$sub" in
        push) _ger_push ;;
        log) _ger_log ;;
        branch) _ger_branch ;;
        edit) _ger_edit ;;
        cid) _ger_cid ;;
        sha) _ger_sha ;;
        show) _ger_show ;;
        comments) _ger_comments ;;
    esac
}

complete -o default -F _ger ger
