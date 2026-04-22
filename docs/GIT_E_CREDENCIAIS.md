# Git, chaves SSH e credenciais — guia detalhado

Este documento explica como trabalhar com **Git** e **GitHub** neste projeto, como configurar **chaves SSH**, onde guardar **credenciais** e o que **nunca** deve ir para o repositório.

---

## 1. Conceitos rápidos

| Conceito | O que é |
|----------|---------|
| **Repositório (repo)** | Histórico de ficheiros + branches (local na tua máquina ou no GitHub). |
| **Remote** | Endereço do servidor Git (ex.: `origin` → GitHub). |
| **Commit** | Um “snapshot” das alterações com mensagem descritiva. |
| **Push** | Enviar commits do teu computador para o remote (GitHub). |
| **Pull / fetch** | Trazer alterações do remote para o teu repo local. |
| **Branch** | Linha de desenvolvimento (ex.: `main`). |

**Repositório oficial deste projeto (exemplo):**  
`https://github.com/TavaresEnok/backupcenter`  
Clone por SSH: `git@github.com:TavaresEnok/backupcenter.git`

---

## 2. Instalar Git

### Linux (Debian/Ubuntu)

```bash
sudo apt update
sudo apt install -y git
git --version
```

### Configurar identidade (aparece nos commits)

```bash
git config --global user.name "O Teu Nome"
git config --global user.email "teu-email@exemplo.com"
```

---

## 3. Chaves SSH (recomendado para GitHub)

O GitHub aceita autenticação por **SSH** (chave pública no site, chave privada só no teu PC) ou por **HTTPS** com **Personal Access Token** (PAT).

### 3.1 Gerar uma chave Ed25519 (recomendado)

```bash
ssh-keygen -t ed25519 -C "github-teu-email@exemplo.com" -f ~/.ssh/id_ed25519_backupcenter
```

- Quando pedir **passphrase**, podes definir uma (mais seguro) ou Enter vazio (mais simples, menos seguro).
- Ficam dois ficheiros:
  - **`~/.ssh/id_ed25519_backupcenter`** — **privada: nunca copiar para o Git, email, chat ou GitHub.**
  - **`~/.ssh/id_ed25519_backupcenter.pub`** — **pública: é esta que se cola no GitHub.**

### 3.2 Ver a chave pública para colar no GitHub

```bash
cat ~/.ssh/id_ed25519_backupcenter.pub
```

### 3.3 Adicionar a chave no GitHub

1. Abrir: [GitHub → Settings → SSH and GPG keys](https://github.com/settings/keys)  
2. **New SSH key**  
3. Título: ex. `Laptop Backup Center`  
4. Colar o conteúdo completo da linha que começa com `ssh-ed25519`  
5. Guardar  

Documentação oficial: [Connecting to GitHub with SSH](https://docs.github.com/en/authentication/connecting-to-github-with-ssh)

### 3.4 Ficheiro `~/.ssh/config` (várias chaves ou nome custom)

Cria ou edita `~/.ssh/config`:

```sshconfig
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519_backupcenter
  IdentitiesOnly yes
```

Permissões:

```bash
chmod 600 ~/.ssh/config
chmod 600 ~/.ssh/id_ed25519_backupcenter
```

### 3.5 Testar ligação ao GitHub

```bash
ssh -T git@github.com
```

Mensagem típica de sucesso: *Hi \<username\>! You've successfully authenticated...*

---

## 4. Clonar o projeto

### Por SSH (com chave configurada)

```bash
cd /caminho/onde/guardas/projetos
git clone git@github.com:TavaresEnok/backupcenter.git
cd backupcenter
```

### Por HTTPS (pede utilizador + token, não password da conta)

```bash
git clone https://github.com/TavaresEnok/backupcenter.git
cd backupcenter
```

Para push via HTTPS, o GitHub exige um **Personal Access Token** em vez da password da conta:  
[Creating a personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)

### GitHub CLI (`gh`) — opcional

Instalação: [GitHub CLI](https://cli.github.com/)

```bash
gh auth login
gh repo clone TavaresEnok/backupcenter
```

---

## 5. Fluxo de trabalho diário (resumo)

```bash
cd /caminho/backupcenter
git status
git pull origin main          # atualizar antes de trabalhar
# ... editar ficheiros ...
git add -A                    # ou git add ficheiros específicos
git commit -m "descricao curta e clara do que mudou"
git push origin main
```

### Boas práticas

- Commits **pequenos** e mensagens **claras** (o que e porquê).
- **Pull** antes de **push** se trabalhas em equipa.
- Usar **branch** para features grandes: `git checkout -b feature/nome` (opcional).

---

## 6. Remote: ver e alterar

```bash
git remote -v
```

Definir `origin` (se clonaste por HTTPS e queres mudar para SSH):

```bash
git remote set-url origin git@github.com:TavaresEnok/backupcenter.git
```

---

## 7. Credenciais e segredos — **não vão para o Git**

### 7.1 O que é segredo

- Passwords de base de dados, Redis, admin da app  
- `SECRET_KEY`, `ENCRYPTION_KEY` (Fernet)  
- Tokens Mercado Pago, webhooks  
- Chaves **privadas** SSH (`.pem`, `id_ed25519` **sem** `.pub`)  
- Conteúdo de **`.env`**  
- Mapas internos tipo `JUMP_HOST_SLOTS_OVERRIDES` com IPs/portas de produção  
- `FLOWER_BASIC_AUTH` com password real  

### 7.2 Onde guardar

- Ficheiro **`.env`** na raiz do projeto (já está no `.gitignore`).  
- Cópia de **`.env`** apenas em cofre: gestor de passwords da equipa, vault da empresa, **nunca** em issues/PRs/README.

Modelo sem segredos: **`.env.example`** na raiz — copiar para `.env` e preencher localmente:

```bash
cp .env.example .env
nano .env   # ou o teu editor
```

### 7.3 Se já commitaste um segredo por engano

1. **Revogar / rodar** imediatamente a credencial (password, token, chave).  
2. Remover do histórico se necessário: [Removing sensitive data from a repository](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository) (ferramentas como `git filter-repo`).  
3. Avisar a equipa — histórico clonado pode ainda conter o segredo.

### 7.4 O que pode ir para o Git

- Código, templates, migrações, testes  
- `docker-compose.yml` **sem** valores secretos embutidos (usar `${VAR}` e `.env`)  
- `README.md` / `docs/*.md` **sem** passwords nem IPs sensíveis  

---

## 8. Ligar este projeto ao GitHub (checklist)

- [ ] Git instalado e `user.name` / `user.email` configurados  
- [ ] Chave SSH gerada; **só** a `.pub` no GitHub  
- [ ] `ssh -T git@github.com` com sucesso  
- [ ] `git clone git@github.com:TavaresEnok/backupcenter.git`  
- [ ] `cp .env.example .env` e preencher **fora** do Git  
- [ ] `git status` não mostra `.env` como ficheiro novo (deve estar ignorado)  

---

## 9. Links úteis

| Recurso | URL |
|---------|-----|
| Documentação Git | https://git-scm.com/doc |
| GitHub Docs (Git) | https://docs.github.com/en/get-started/git-basics |
| SSH keys GitHub | https://docs.github.com/en/authentication/connecting-to-github-with-ssh |
| Personal Access Token | https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens |
| Remover dados sensíveis | https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository |

---

## 10. Plano de melhoria em produção

Para deploy seguro, checkpoints e gates, ver também: **`docs/PLANO_MELHORIA_PRODUCAO.md`**.

---

*Última atualização: documento de apoio à equipa; mantém-se sem credenciais reais.*
