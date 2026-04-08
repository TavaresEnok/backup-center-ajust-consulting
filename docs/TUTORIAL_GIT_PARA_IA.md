# Tutorial Git Para Outra IA (SSH + Push)

Este guia e para qualquer IA/automacao operar o Git no Backup Center com seguranca.

## 1) Pre-requisitos

- Ter acesso ao servidor/workspace do projeto.
- Ter `git` instalado.
- Ter acesso ao GitHub do repositorio.

Repositorio atual:

```bash
git remote -v
# esperado: github.com:TavaresEnok/backupcenter.git
```

## 2) Configurar identidade Git

```bash
git config --global user.name "Backup Center Bot"
git config --global user.email "bot@ajustconsulting.com.br"
```

Verificar:

```bash
git config --global --get user.name
git config --global --get user.email
```

## 3) Criar chave SSH (ed25519)

```bash
ssh-keygen -t ed25519 -C "bot@ajustconsulting.com.br"
# arquivo sugerido: /home/USUARIO/.ssh/id_ed25519
# senha da chave: opcional (recomendado com passphrase)
```

Permissoes:

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_ed25519
chmod 644 ~/.ssh/id_ed25519.pub
```

## 4) Subir chave no agente SSH

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
```

## 5) Cadastrar chave publica no GitHub

Mostrar chave publica:

```bash
cat ~/.ssh/id_ed25519.pub
```

No GitHub:

- Settings
- SSH and GPG keys
- New SSH key
- Cole a chave publica

## 6) Testar autenticacao SSH no GitHub

```bash
ssh -T git@github.com
```

Saida esperada (exemplo):

```text
Hi <usuario>! You've successfully authenticated...
```

## 7) Garantir que o remote usa SSH

```bash
git remote set-url origin git@github.com:TavaresEnok/backupcenter.git
git remote -v
```

## 8) Fluxo padrao (safe) para a IA

No diretorio do projeto:

```bash
cd /srv/backup_center_new
git status --short --branch
```

Atualizar branch local:

```bash
git fetch origin
git pull --rebase origin main
```

Adicionar mudancas e commitar:

```bash
git add -A
git commit -m "tipo: descricao curta"
```

Enviar:

```bash
git push origin main
```

## 9) Convencao de mensagem de commit

Padrao recomendado:

```text
feat: nova funcionalidade
fix: correcao de bug
chore: tarefa operacional/infra
docs: atualizacao de documentacao
refactor: melhoria sem mudar comportamento
```

## 10) Regras de seguranca para IA

- Nao usar `git reset --hard`.
- Nao usar `git checkout -- .`.
- Nao apagar alteracoes de terceiros sem confirmacao.
- Sempre rodar `git status` antes de commitar.
- Se houver muitas mudancas nao relacionadas, preferir commits separados por tema.

## 11) Erros comuns e como resolver

### `Permission denied (publickey)`

- Chave SSH nao cadastrada no GitHub.
- `ssh-agent` sem chave carregada.
- Remote ainda em HTTPS.

Comandos uteis:

```bash
ssh-add -l
git remote -v
ssh -T git@github.com
```

### `Please tell me who you are`

Configure `user.name` e `user.email` (secao 2).

### `non-fast-forward`

Branch local atrasada. Execute:

```bash
git pull --rebase origin main
# depois
git push origin main
```

### Conflitos no rebase

```bash
git status
# resolver arquivos
git add <arquivos>
git rebase --continue
```

## 12) Opcional: usar token (HTTPS) em vez de SSH

Se SSH nao for possivel, usar PAT do GitHub (escopo `repo`) e remote HTTPS.
Recomendado somente quando SSH estiver indisponivel.

## 13) Checklist rapido para outra IA

Antes do push:

- [ ] `git status` limpo do que nao deve subir
- [ ] commit com mensagem clara
- [ ] branch correta (`main` ou feature)
- [ ] autenticacao valida (`ssh -T git@github.com`)

Depois do push:

- [ ] confirmar hash enviado
- [ ] confirmar no GitHub se commit apareceu

---

Se a IA for operar em producao, sempre registrar no log de operacao:

- hash do commit
- horario
- arquivos afetados
- comando de deploy/restart executado
