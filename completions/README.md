# Shell completion

Bash:
```bash
source /path/to/kakao-cli/completions/kakao.bash
# or copy to /etc/bash_completion.d/kakao
```

Zsh (put the `_kakao` file on your `$fpath`):
```bash
mkdir -p ~/.zsh/completions && cp completions/_kakao ~/.zsh/completions/
# in ~/.zshrc, before compinit:
fpath=(~/.zsh/completions $fpath)
autoload -Uz compinit && compinit
```
