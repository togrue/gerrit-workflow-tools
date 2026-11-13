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

_ger_cache() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [ "${COMP_CWORD:-0}" -eq 2 ]; then
        if [[ "$cur" == -* ]]; then
            __gwt_flags "$cur" -h --help --color --hyperlinks --debug-log
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
        --hyperlinks)
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
        __gwt_flags "$cur" -h --help --debug-log --start-at-remote --check --fix --color --hyperlinks
        return
    fi
    case "$prev" in
        --color)
            __gwt_flags "$cur" always auto never
            return
            ;;
        --hyperlinks)
            __gwt_flags "$cur" always auto never
            return
            ;;
    esac
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_checkout() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help --patchset -b --branch --update --no-update --force --color --hyperlinks --debug-log
        return
    fi
    case "$prev" in
        --color)
            __gwt_flags "$cur" always auto never
            return
            ;;
        --hyperlinks)
            __gwt_flags "$cur" always auto never
            return
            ;;
    esac
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_config() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help --color --hyperlinks --debug-log
        return
    fi
    case "$prev" in
        --color)
            __gwt_flags "$cur" always auto never
            return
            ;;
        --hyperlinks)
            __gwt_flags "$cur" always auto never
            return
            ;;
    esac
}

_ger_edit() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help --first-attention-commit -n --next --cursor --cursor-history --set-cursor --reword --drop --debug-log
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
        __gwt_flags "$cur" -h --help --compact --debug-log
        return
    fi
}

_ger_fix() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help -a --all --no-verify --json --debug-log
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_inbox() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help --to-review --outgoing --project --all --unready --limit --json --url --show-url --no-url --color --hyperlinks -v --verbose --debug-log
        return
    fi
    case "$prev" in
        --color)
            __gwt_flags "$cur" always auto never
            return
            ;;
        --hyperlinks)
            __gwt_flags "$cur" always auto never
            return
            ;;
    esac
}

_ger_log() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help --json --color --hyperlinks --url --show-url --show-change-id -v --verbose --debug-log --follow-merges
        return
    fi
    case "$prev" in
        --color)
            __gwt_flags "$cur" always auto never
            return
            ;;
        --hyperlinks)
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
        __gwt_flags "$cur" -h --help -i --branch --dry-run --no-rebase-check -y --yes --publish-comments --all --color --hyperlinks --follow-merges --reviewers --reviewer-strategy --topic --wip --ready --private --no-private --debug-log
        return
    fi
    case "$prev" in
        --color)
            __gwt_flags "$cur" always auto never
            return
            ;;
        --hyperlinks)
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
        __gwt_flags "$cur" -h --help --onto-remote --no-onto-remote --drop-merged-equivalent --autosquash --debug-log
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_resolve() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help --json --color --hyperlinks --debug-log
        return
    fi
    case "$prev" in
        --color)
            __gwt_flags "$cur" always auto never
            return
            ;;
        --hyperlinks)
            __gwt_flags "$cur" always auto never
            return
            ;;
    esac
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_ger_reword() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" -h --help --first-attention-commit -n --next --cursor --cursor-history --set-cursor --edit --drop --debug-log
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
        __gwt_flags "$cur" -h --help --first-attention -n --next-attention --range --all --short --subject --json --color --hyperlinks --debug-log
        return
    fi
    case "$prev" in
        --color)
            __gwt_flags "$cur" always auto never
            return
            ;;
        --hyperlinks)
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
        __gwt_flags "$cur" -h --help --stack --first-attention -n --next-attention --branch --comments --context --context-padding --ci --no-ci --json --format --ai --color --hyperlinks -v --verbose --debug-log
        return
    fi
    case "$prev" in
        --color)
            __gwt_flags "$cur" always auto never
            return
            ;;
        --comments)
            __gwt_flags "$cur" unresolved all resolved
            return
            ;;
        --format)
            __gwt_flags "$cur" human markdown
            return
            ;;
        --hyperlinks)
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
        __gwt_flags "$cur" bash-completion cache change-id changeid checkout ci comment config edit fetch-api fix inbox log push rebase reply resolve restack review reword serve setup sha show stack vscode
        return
    fi
    local sub="${COMP_WORDS[1]}"
    case "$sub" in
        bash-completion) _ger_bash_completion ;;
        cache) _ger_cache ;;
        change-id|changeid) _ger_change_id ;;
        checkout) _ger_checkout ;;
        ci) _ger_ci ;;
        comment) _ger_comment ;;
        config) _ger_config ;;
        edit) _ger_edit ;;
        fetch-api) _ger_fetch_api ;;
        fix) _ger_fix ;;
        inbox) _ger_inbox ;;
        log) _ger_log ;;
        push) _ger_push ;;
        rebase|restack|stack) _ger_rebase ;;
        reply) _ger_reply ;;
        resolve) _ger_resolve ;;
        review) _ger_review ;;
        reword) _ger_reword ;;
        serve) _ger_serve ;;
        setup) _ger_setup ;;
        sha) _ger_sha ;;
        show) _ger_show ;;
        vscode) _ger_vscode ;;
    esac
}

complete -o default -F _ger ger
