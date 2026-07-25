# Git repositories attached to a Library space

A space groups a piece of work — its notes, its board, its files. Attaching the
repositories that work actually happens in lets Prax read the code, see what
changed, and connect a task to the branch that implements it.

It also hands an agent a credential and a checkout, so the interesting part is
what it is **not** allowed to do.

## Off by default

```bash
SPACE_REPOS_ENABLED=true
```

Until then the `space_repo_*` tools are not in the agent's toolset at all —
absent rather than present-and-refusing. A tool the agent can see is a tool it
will try, and spending a turn discovering a feature is disabled is worse than
never offering it.

Turning it on does **not** grant pushing. That is a second, per-repository
decision, and it starts off no matter what this flag says.

## The three guarantees

| Guarantee | Mechanism |
|---|---|
| A repo is scoped to one space | Cloned to `library/spaces/{slug}/repos/{name}/`; name validated **and** resolved path re-checked against the space root |
| Prax cannot push unless a human said so | `write: false` on every attachment; `space_repo_set_write` is the only way on, **per repository** |
| A leaked credential exposes one repo | One **ed25519 deploy key per attachment**, `IdentitiesOnly=yes`, stored outside the workspace at `~/.prax/git-keys` mode 0600 |

## Why a deploy key and not an SSH key

**An ordinary SSH key cannot be restricted.** It authenticates *the account*, so
it reaches every repository that account can reach. Handing one to an agent to
work on a single repo grants it everything.

A **deploy key is registered on exactly one repository.** Read-only unless you
tick *Allow write access* on that repo. So the blast radius of a compromised
agent is one repo, and you can see which by looking at where the key is
installed.

If you would rather have one credential covering a chosen *set* of repos, a
**fine-grained PAT** with an explicit repository list and `contents: read` (or
`read/write`) is the other correctly-scoped option. What is *not* correctly
scoped: an account SSH key, or a classic PAT.

`GIT_SSH_COMMAND` sets **`IdentitiesOnly=yes`**. Without it ssh offers every key
it can find, and a repository could be reached with a credential that was never
meant for it — silently undoing the scoping this whole design exists for.

## Attaching

```
space_repo_add(space_slug, url, name)
```

Clones **read-only** and returns the public half of a freshly generated deploy
key. Add it to the repository (GitHub: *Settings → Deploy keys*); a private repo
cannot be cloned until you do. Tick *Allow write access* **only** if you intend
to let Prax push.

## The write toggle

```
space_repo_set_write(space_slug, name, enabled=True)
```

Off by default on every attachment, and **per repository** — enabling it for one
leaves the others refusing. Reading a repository and pushing to it are different
risk classes; the second should require a decision, and enabling it everywhere
because it was needed in one place is how a narrow permission quietly becomes a
broad one.

Note that **two** things must be true to push: write enabled here, *and* the
deploy key granted write access on the repository itself. The tool says so when
you enable it, because discovering the second one via a rejected push wastes
your time.

## Isolation, and one hazard worth knowing

Checkouts live under their space and cannot escape it. Names are restricted to
`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, and the *resolved* path is compared against
the space root afterwards — a symlink pointing outside passes the name check and
fails the second one.

**The workspace is itself a git repository** that runs `git add -A` and commits
everything not ignored. Cloning other repositories inside it would either
swallow their contents into the user's workspace history or record a broken
gitlink. So `library/spaces/*/repos/` is in the workspace `.gitignore`, and
because Prax refreshes stale gitignores on startup, existing workspaces pick this
up without intervention.

For the same reason **deploy keys are never stored inside the workspace** — a
private key written there would end up committed.

## What this does not do yet

- **No multi-tenant story.** Like the sandbox, this assumes one operator. The
  per-space isolation is real, but it is isolation *within* one user's
  workspace, not between customers.
- **No PAT support.** Deploy keys only for now; a fine-grained PAT path is the
  natural addition for someone attaching many repos at once.
- **No automatic branch/task linking.** The pieces exist —
  `ensure_branch_channel` on the TeamWork side, tasks in `.tasks.yaml` — but
  nothing wires a Kanban card to a branch automatically yet.
