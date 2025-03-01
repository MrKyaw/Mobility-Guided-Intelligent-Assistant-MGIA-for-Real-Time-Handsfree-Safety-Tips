# Security notes

A previous revision committed `config/.env` with a Together API key. It has been
removed and replaced with `config/.env.example`. `.env` files are ignored.

**Rotate that key** in the Together dashboard and audit usage for unexpected
charges. Removing the file from the latest commit does not remove it from git
history; rewrite history with `git filter-repo` or BFG if you need to purge it.

## Prevent recurrence

- Keep secrets in `.env` or a managed secret store, never in tracked files.
- Commit only `.env.example` with empty or placeholder values.
- Run `git grep -nE "(api[_-]?key|secret|token)\\s*=\\s*['\"]?[A-Za-z0-9]"` before publishing.
