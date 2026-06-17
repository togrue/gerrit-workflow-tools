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

_ger_bash_completion() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help --install --uninstall --rc-file
        return
    fi
}

_ger_setup() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help --local
        return
    fi
}

_ger_cache() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [ "${COMP_CWORD:-0}" -eq 2 ]; then
        if [[ "$cur" == -* ]]; then
            __gwt_flags "$cur" -h --help --color -v --verbose --debug-log
        else
            __gwt_flags "$cur" clear info
        fi
        return
    fi
    local sub="${COMP_WORDS[2]}"
    case "$prev" in
        --color)
            __gwt_flags "$cur" always auto never
            return
            ;;
    esac
    if [[ "$cur" == -* ]]; then
        case "$sub" in
            clear)
                __gwt_flags "$cur" -h --help
                ;;
            info)
                __gwt_flags "$cur" -h --help
                ;;
        esac
        return
    fi
    case "$sub" in
    esac
}

_ger_change_id() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help -v --verbose --debug-log --start-at-remote --check-duplicates --fix --color
        return
    fi
    case "$prev" in
        --color)
            __gwt_flags "$cur" always auto never
            return
            ;;
    esac
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_edit() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help --first-attention-commit --reword --drop -v --verbose --debug-log
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_fetch_api() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help --compact -v --verbose --debug-log
        return
    fi
}

_ger_fix() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help -a --all --no-verify -v --verbose --debug-log
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_log() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help --json --color --url --show-url --show-change-id -v --verbose --debug-log --follow-merges
        return
    fi
    case "$prev" in
        --color)
            __gwt_flags "$cur" always auto never
            return
            ;;
    esac
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_push() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help -i --branch --dry-run --no-rebase-check -y --yes --all --color --ignore-pattern --follow-merges --reviewers --reviewer-strategy --topic --wip --private -v --verbose --debug-log
        return
    fi
    case "$prev" in
        --color)
            __gwt_flags "$cur" always auto never
            return
            ;;
        --reviewer-strategy)
            __gwt_flags "$cur" push lazy overwrite
            return
            ;;
    esac
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_rebase() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help --onto-remote --no-onto-remote --drop-merged-equivalent -v --verbose --debug-log
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_reword() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help --first-attention-commit --edit --drop -v --verbose --debug-log
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_sha() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help --range --all --short --subject --json --color -v --verbose --debug-log
        return
    fi
    case "$prev" in
        --color)
            __gwt_flags "$cur" always auto never
            return
            ;;
    esac
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_show() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help --full --comment-tail-lines --json --color -v --verbose --debug-log
        return
    fi
    case "$prev" in
        --color)
            __gwt_flags "$cur" always auto never
            return
            ;;
    esac
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [ "${COMP_CWORD:-0}" -eq 1 ]; then
        __gwt_flags "$cur" bash-completion cache change-id changeid edit fetch-api fix log push rebase restack reword setup sha show stack
        return
    fi
    local sub="${COMP_WORDS[1]}"
    case "$sub" in
        bash-completion) _ger_bash_completion ;;
        cache) _ger_cache ;;
        change-id|changeid) _ger_change_id ;;
        edit) _ger_edit ;;
        fetch-api) _ger_fetch_api ;;
        fix) _ger_fix ;;
        log) _ger_log ;;
        push) _ger_push ;;
        rebase|restack|stack) _ger_rebase ;;
        reword) _ger_reword ;;
        setup) _ger_setup ;;
        sha) _ger_sha ;;
        show) _ger_show ;;
    esac
}

complete -o default -F _ger ger
