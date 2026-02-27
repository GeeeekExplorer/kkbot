# GitHub CLI (gh) Skill

Use `gh` to interact with GitHub from the command line. Always set proxy before network operations:
```bash
export https_proxy=http://45.118.133.155:2345 http_proxy=http://45.118.133.155:2345
```

## Auth

```bash
gh auth status                        # Check login status
gh auth login                         # Interactive login
GH_TOKEN=<token> gh auth status       # Use token directly
```

## Repo

```bash
gh repo create <name> --public/--private --description "..." --source=. --push
gh repo create <name> --public --clone
gh repo list [owner]                  # List repos
gh repo view [owner/repo]             # View repo info
gh repo clone owner/repo
gh repo fork owner/repo --clone
gh repo edit --description "..." --visibility public/private
gh repo delete owner/repo --yes
gh repo set-default owner/repo        # Set default repo for gh commands

# gitignore helpers (generates content, pipe to file)
gh repo gitignore view Python > .gitignore
gh repo gitignore list                # List available templates
```

## Issues

```bash
gh issue list [-s open/closed] [-l label] [-a assignee]
gh issue create -t "Title" -b "Body" [-l label]
gh issue view <number>
gh issue close <number>
gh issue reopen <number>
gh issue edit <number> --title "..." --body "..."
```

## Pull Requests

```bash
gh pr list
gh pr create -t "Title" -b "Body" [-B base-branch]
gh pr view <number>
gh pr checkout <number>
gh pr merge <number> --merge/--squash/--rebase
gh pr review <number> --approve/--request-changes/--comment
gh pr close <number>
gh pr checks <number>
```

## Releases

```bash
gh release list
gh release create v1.0.0 --title "v1.0.0" --notes "Release notes"
gh release create v1.0.0 ./dist/* --title "v1.0.0"
gh release view v1.0.0
gh release delete v1.0.0 --yes
gh release download v1.0.0 -p "*.tar.gz"
```

## Workflows / Actions

```bash
gh workflow list
gh workflow run <workflow-name>
gh run list
gh run view <run-id>
gh run watch <run-id>
gh run rerun <run-id>
```

## Gists

```bash
gh gist create file.txt --public -d "Description"
gh gist list
gh gist view <id>
gh gist edit <id>
```

## Misc

```bash
gh browse                             # Open repo in browser
gh browse <issue/pr number>
gh api /repos/owner/repo              # Raw API call
gh search repos "query"
gh search issues "query" --repo owner/repo
gh status                             # Notifications overview
```

## Tips

- Set `GH_TOKEN` env var to authenticate non-interactively in scripts
- Use `--json fields -q '.field'` with jq for scripting: `gh issue list --json number,title -q '.[].title'`
- `gh repo create --source=. --push` creates a remote repo and pushes current directory in one step
