# bash completion for kakao-cli.  Install:
#   source completions/kakao.bash          (or copy to /etc/bash_completion.d/)
_kakao() {
    local cur prev words cword
    _init_completion 2>/dev/null || { cur="${COMP_WORDS[COMP_CWORD]}"; prev="${COMP_WORDS[COMP_CWORD-1]}"; }
    local cmds="doctor rooms read send memo watch send-file pull-media track poll"
    local common="--json --serial --connect --adb -h --help"

    # find the subcommand (first non-option word after 'kakao')
    local i sub=""
    for ((i=1; i<COMP_CWORD; i++)); do
        case "${COMP_WORDS[i]}" in
            -*) ;; *) sub="${COMP_WORDS[i]}"; break ;;
        esac
    done

    if [[ -z "$sub" ]]; then
        COMPREPLY=( $(compgen -W "$cmds --version -h --help" -- "$cur") )
        return
    fi

    # per-subcommand options
    local opts="$common"
    case "$sub" in
        read)        opts+=" --limit" ;;
        send)        opts+=" --yes --home" ;;
        memo)        opts+=" --home" ;;
        watch)       opts+=" --interval" ;;
        send-file)   opts+=" --yes --home" ;;
        pull-media)  opts+=" --new-only" ;;
        track)       opts+=" --interval --window" ;;
        poll)        opts+=" --interval --limit" ;;
    esac

    # file-arg completion
    case "$sub:$prev" in
        send-file:send-file) COMPREPLY=(); return ;;  # first positional = target
        send-file:*) if [[ "$cur" != -* ]]; then _filedir; return; fi ;;
        pull-media:pull-media) _filedir -d; return ;;
    esac

    if [[ "$cur" == -* ]]; then
        COMPREPLY=( $(compgen -W "$opts" -- "$cur") )
    fi
}
complete -F _kakao kakao
