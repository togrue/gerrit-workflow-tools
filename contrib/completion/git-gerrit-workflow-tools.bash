# Bash completion for gerrit-workflow-tools git wrappers (git gpush, git glog, …).
# shellcheck disable=SC2207,SC2154
#
# Install: source this file from ~/.bashrc after git's bash completion, or see docu/Completion.md
#
# Requires: Git's bash completion (for __git_complete). Optional: __git_complete_refs for revision args.

__gwt_have_git_complete() {
    declare -F __git_complete >/dev/null 2>&1
}

__gwt_flags() {
    local cur=$1
    shift
    COMPREPLY=( $(compgen -W "$*" -- "$cur") )
}

_git_gpush() {
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
            -v --verbose
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_git_glog() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" \
            --help \
            --full \
            --oneline --no-oneline \
            --compact --no-compact \
            --json \
            --no-color \
            --url --show-url \
            --show-change-id \
            -v --verbose
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_git_gbranch() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [ "${COMP_CWORD:-0}" -eq 2 ]; then
        __gwt_flags "$cur" show init set-target set-reviewers set-push-mode
        return
    fi
    local sub="${COMP_WORDS[2]}"
    if [[ "$cur" == -* ]]; then
        case "$sub" in
            init)
                __gwt_flags "$cur" --help --target --reviewers --push-mode -v --verbose
                ;;
            show|set-target|set-reviewers|set-push-mode)
                __gwt_flags "$cur" --help -v --verbose
                ;;
            *)
                __gwt_flags "$cur" --help -v --verbose
                ;;
        esac
        return
    fi
}

_git_gedit() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" --help --reword --drop -v --verbose
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_git_gcid() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" --help --check-duplicates --start-at-remote -v --verbose
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_git_gsha() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" \
            --help \
            --range \
            --all \
            --short \
            --subject \
            --json \
            -v --verbose
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_git_gshow() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [[ "$cur" == -* ]]; then
        __gwt_flags "$cur" \
            --help \
            --full \
            --comment-tail-lines \
            --json \
            --no-color \
            -v --verbose
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

_git_gcomments() {
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
            -v --verbose
        return
    fi
    if declare -F __git_complete_refs >/dev/null 2>&1; then
        __git_complete_refs
    fi
}

# Register with git's completion dispatcher (git 1.7+; needs git's bash completion loaded first).
if __gwt_have_git_complete; then
    __git_complete gpush _git_gpush
    __git_complete glog _git_glog
    __git_complete gbranch _git_gbranch
    __git_complete gedit _git_gedit
    __git_complete gcid _git_gcid
    __git_complete gsha _git_gsha
    __git_complete gshow _git_gshow
    __git_complete gcomments _git_gcomments
fi

# Standalone launchers: git-gpush etc. (COMP_WORDS[0] is the executable name)
__gwt_wrap_git_sub() {
    local fn=$1
    shift
    local exe=$1
    local base=${exe##*/}
    base=${base%.exe}
    local sub=${base#git-}
    local _w=( "${COMP_WORDS[@]}" )
    local _cw=$COMP_CWORD
    COMP_WORDS=(git "$sub" "${_w[@]:1}")
    COMP_CWORD=$((_cw + 1))
    "$fn"
    COMP_WORDS=( "${_w[@]}" )
    COMP_CWORD=$_cw
}

_git_gpush_standalone() { __gwt_wrap_git_sub _git_gpush "${COMP_WORDS[0]}"; }
_git_glog_standalone() { __gwt_wrap_git_sub _git_glog "${COMP_WORDS[0]}"; }
_git_gbranch_standalone() { __gwt_wrap_git_sub _git_gbranch "${COMP_WORDS[0]}"; }
_git_gedit_standalone() { __gwt_wrap_git_sub _git_gedit "${COMP_WORDS[0]}"; }
_git_gcid_standalone() { __gwt_wrap_git_sub _git_gcid "${COMP_WORDS[0]}"; }
_git_gsha_standalone() { __gwt_wrap_git_sub _git_gsha "${COMP_WORDS[0]}"; }
_git_gshow_standalone() { __gwt_wrap_git_sub _git_gshow "${COMP_WORDS[0]}"; }
_git_gcomments_standalone() { __gwt_wrap_git_sub _git_gcomments "${COMP_WORDS[0]}"; }

complete -o default -F _git_gpush_standalone git-gpush
complete -o default -F _git_glog_standalone git-glog
complete -o default -F _git_gbranch_standalone git-gbranch
complete -o default -F _git_gedit_standalone git-gedit
complete -o default -F _git_gcid_standalone git-gcid
complete -o default -F _git_gsha_standalone git-gsha
complete -o default -F _git_gshow_standalone git-gshow
complete -o default -F _git_gcomments_standalone git-gcomments
