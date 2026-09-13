## Privacy Policy

### Data Collection

This plugin collects and stores, as plugin credentials, exactly what you provide in its configuration screen:

- An SSH private key and (optional) passphrase, if you choose the "SSH deploy key" authentication mode. No credentials are collected for public repositories.
- A Dify API key and API base URL, only if you enable the optional "verify against existing knowledge base documents" feature on a datasource node.
- Per-pipeline-node configuration you enter (repository URL, branch, file extension/path filters, dataset ID, and the two feature toggles). These are not secrets and are stored as ordinary pipeline configuration by Dify.

The plugin does not collect personal data about you or end users beyond what is inherent in the git commit metadata it reads (e.g. commit timestamps) from the repositories you configure it to access.

### Data Usage

- The SSH private key is used solely to authenticate outbound SSH connections to the git host(s) you configure, in order to read repository content. It is never transmitted anywhere other than the git host itself (as part of standard SSH key authentication) and is never logged.
- File content fetched from configured repositories is passed into the Dify Knowledge Pipeline you configured this datasource in, to be ingested into a knowledge base. No content is sent to any third-party service beyond the git host you specified.
- If "verify against existing knowledge base documents" is enabled, the Dify API key is used to call your own Dify instance's Dataset API (`GET /v1/datasets/{dataset_id}/documents`) to read the list of document names already present in the target Knowledge Base, purely to decide which files still need syncing. No document content is read or modified through this call.

### Data Retention

Credentials persist until you remove/reconfigure this datasource or uninstall the plugin, at which point Dify removes the associated stored data. This plugin does not keep any sync-state of its own between runs; every run's behavior is determined solely by the current configuration, the current state of the git repository, and (if enabled) the current state of the target Knowledge Base.

### Security notes

- SSH host key verification defaults to trust-on-first-use rather than strict verification against a pre-shared `known_hosts` file, because the plugin's sandboxed runtime has no persistent location for you to supply one. This is a deliberate trade-off appropriate for a read-only deploy key; if this does not meet your security requirements, do not use SSH authentication with this plugin.
- The Dify API key you provide (for the optional dataset-verification feature) should be scoped as narrowly as your Dify instance's API key system allows, since it grants read access to your Knowledge Base's document list.

### Contact

For privacy-related questions about this plugin, contact the plugin author.
