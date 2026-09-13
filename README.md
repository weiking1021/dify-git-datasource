## git

**Author:** weiking1021
**Version:** 0.0.1
**Type:** datasource

### Description

Sync files from any git repository (public, or private via an SSH deploy key) into a Dify Knowledge Pipeline. Unlike the official GitHub/GitLab datasources, this plugin talks to git's own transfer protocol directly (via the pure-Python `dulwich` library) instead of a hosting provider's REST API, so it works against self-hosted Gitea/GitLab/Bitbucket instances or any plain git remote. It never shells out to a `git` or `ssh` binary - SSH auth is handled by `paramiko`, also pure Python - so it has no dependency on the plugin runtime having git installed.

### Setup

1. Install the plugin and open its credential settings.
2. Choose an authentication mode:
   - **No authentication** - for public repositories.
   - **SSH deploy key** - paste a private key (OpenSSH, PEM, or PKCS8; RSA, Ed25519, or ECDSA) and, if it's passphrase-protected, the passphrase. Generate a read-only deploy key on your git host and register its public half against the target repository.
3. (Optional, only needed for the "verify against existing documents" feature below) Set **Dify API base URL** and **Dify API key**. The base URL defaults to `http://api:5001`, a best-effort guess for the standard self-hosted docker-compose layout - Dify's plugin sandbox deliberately never exposes its own instance's address to plugin code, so this can't be auto-detected and may need changing for your deployment (Kubernetes, serverless plugin execution, a renamed `api` service, etc.).
4. In a Knowledge Pipeline, add this datasource and configure, per node:
   - **Repository URL** - an HTTPS URL for public repos, or an SSH URL / `git@host:org/repo.git` shorthand for private repos.
   - **Branch or ref** - defaults to the repository's default branch (`HEAD`).
   - **File extensions to include** - comma-separated, e.g. `.md,.mdx,.txt`.
   - **Path filter** - comma-separated directory prefixes or glob patterns, e.g. `docs/,guides/**/*.md`.
   - **Verify against existing knowledge base documents** - off by default. Every run simply lists whatever matching files git currently has; nothing is tracked, so nothing can get out of sync. Turn this on to instead query the target Knowledge Base's actual documents (via Dify's own Dataset API) and skip files that already exist there - this needs the Dify API key/base URL above and the dataset ID below.
   - **Target Knowledge Base dataset ID** - only needed when the toggle above is on. Copy it from the Knowledge Base's own settings/API page.
   - **Append commit hash to file names** - an independent setting. When on, each file's listed name gets a short commit hash before its extension (`guide@a1b2c3d4e5f6.md` instead of `guide.md`). Combined with "verify against existing documents", this makes an updated file re-sync as a new document instead of being skipped forever once any version of it exists - at the cost of leaving the old version's document behind in the Knowledge Base.

### Usage

Once configured, the datasource lists matching files as pipeline pages; selecting pages fetches their raw text content into the knowledge base. Only UTF-8 text files are supported (binary files will error if selected).

### Known limitations

- Content is text-only; binary files aren't supported.
- Without "verify against existing knowledge base documents", every run lists every matching file - there is no incremental/change-detection mode that doesn't call out to Dify's Dataset API, since a plugin-side "last synced" marker was found to be unreliable (a pipeline preview/test-run calls the exact same plugin code as a real run, with no way to tell them apart, so it would silently mark files as synced without anything actually being ingested).
- "Verify against existing knowledge base documents" matches by document name, so if left combined with "append commit hash to file names" off, a file is only ever ingested once - later edits to it are not re-synced. Turning the commit-hash tag on fixes that, but leaves old versions' documents in the Knowledge Base rather than replacing them.
- Each selected file is fetched independently (no shared clone across files), so selecting many files does one network fetch per file.
- SSH host key verification is trust-on-first-use (not strict) - see PRIVACY.md for why.
