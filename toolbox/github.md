# Connected GitHub adapter

Version 0.1.0. Read-only; re-discover tool availability on each host. No credentials are stored here.

## Connector capabilities

- get_user_login({}) returns structuredContent.login and id. Use its login rather than guessing an owner.
- list_repositories({owner: string, page_offset: integer >=0, page_size: positive integer, include_search_index_status: boolean}) returns structuredContent.repositories, an array. Each inspected entry includes id, repository_full_name, owner.login, default_branch, archived and visibility. page_offset is zero-based; page_size is a count, not a page number.
- list_repositories_by_affiliation({affiliation: "owner", page_offset, page_size}) is an alternative enumerator; choose one and preserve its scope.
- fetch({url: string}) reads approved github.com/api.github.com/raw.githubusercontent.com URLs via GET. Git tree, ref and contents endpoints can obtain commit/tree identities. Responses preserve provider JSON; detect errors and tree truncation. No arbitrary hosts or binary downloads.
- fetch_file({repository_full_name: "owner/name", path: string, ref: known commit SHA, encoding: "utf-8"|"base64", start_line?: positive integer, end_line?: positive integer}) reads exact files. Missing ref selects the default branch, so do not omit it in pinned runs. Partial line reads are not complete source evidence.

These names are capabilities; in Codex discover their current GitHub tool namespace. Inspect the current schema rather than constructing a guessed tool name. Purpose: identity, enumeration, immutable source reads. No tool in this adapter writes remotely.

## Pagination and coverage

Use owner from authenticated identity and page_size 100 where supported. Start offset 0, advance by the actual number of entries, retain page metadata, and stop only at an observed empty/short terminal page per provider behavior. The inspected response has no total count or next cursor. An error is never a terminal empty page. Detect repeated pages/IDs, changes during enumeration and unexpected shape; preserve partial coverage on ambiguity. Additional organization/collaborator scope must come from explicit configuration.

Inventory completion describes accessible repository enumeration, not unrestricted account access. Record permission and indexing gaps. For recursive Git trees, inspect truncated; fetch subtrees or use an authorized isolated checkout if needed. Never infer no Python from an incomplete tree. Fetch readme/skill/agent content to distinguish actual workflows from matching filenames.

## Failures and effects

Missing/invalid owner, wrong page units, malformed response, absent source ref, authentication failure and unavailable file are distinct observations. Validate local arguments before sending. Do not retry deterministic errors. A transient read can be retried once within shared bounds; preserve request identity and last error. A failed page keeps coverage incomplete.

Calls are read-only; local normalized manifests and sanitized receipts are durable outputs to index. Credentials and unnecessary private content stay out of diagnostics. Future connector access is conditional on the active account and host.
