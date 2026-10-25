# Open classification questions

These came out of the first independent audit of the registry (October 2026). Each is a genuine judgment call where reasonable people could land on different classes. They are good places to start contributing: pick one, argue it in an issue, and propose a change.

The must-fix and should-fix items from that audit have already been applied.

## Cost and billing
1. **Brave Search and Google Maps tools are R0**, but each query is billed. Does a per-call cost to the principal make a read R2?
2. **`supabase/confirm_cost` is R0.** It only returns a token, but the agent can mint its own approval for a billable create. Is that a class question or a policy question?

## Side effects of "safe" actions
3. **`git/git_commit` is R1**, but git runs repository hooks (`pre-commit`) — arbitrary code from the repository. Should hook-running tools be R4 like other code execution, or is code the principal owns different? (`git_checkout` is now R4 on other grounds; see below.)
4. ~~**`git/git_add` is R1**~~ Resolved: now R3 (see below).
5. **`playwright/browser_hover` is R0**, but pages can act on mouse-over.
6. **`github/create_branch` is R1**, but pushes can trigger workflows — and running a workflow directly (`actions_run_trigger`) is R4.
7. ~~**`filesystem/edit_file` is R1**~~ Resolved: now R4 (see below). The general question is answered by the v0.2.0 definition of *restorable* (SPEC section 3): the prior state must be held by the system or captured before the call (SPEC 5.6). A diff that only the agent holds counts once a gateway captures it.

## What counts as public
8. **Secret gists (`github/create_gist` without `public`) are R1**, but anyone with the URL can read one.
9. **`github/star_repository` is R1**, but stars are publicly visible.
10. **`github/fork_repository` is R1**, but forking into an organization can expose content to people who could not see it before.
11. **Text fields in GitHub Projects** are lowered to R2 on update, but in a public project they are published content.
12. ~~**`sentry/link_issue` is R1**~~ Resolved: now R3, or R2 for native integrations (see below).

## Credentials and safety controls
13. **`sentry/create_project` is R1**, but it provisions a client key; `create_dsn` is R3.
14. ~~**`terraform/update_workspace` is R2**~~ Resolved: R3 unless `auto_apply` is absent or false (see below).
15. **`terraform/create_workspace` is R1**, but with a VCS repository and auto-apply on, later pushes apply without confirmation.
16. **`terraform/detach_variable_set_from_workspaces` and `attach_policy_set_to_workspaces` are R1**, but later runs can fail or be blocked.

## Running workloads
17. **`kubernetes/vm_lifecycle` start/unpause is R1**, but starting runs the VM's workload; `supabase/restore_project` is R2.
18. **`kubernetes/vm_create_from_template` is R1**, but a template can start the VM immediately.
19. **Deleting a bare Pod is R3**, but its `emptyDir` data is lost.
20. **`sentry/analyze_issue_with_seer` is R2**, but organization settings may let it open pull requests.
21. **`sentry/create_uptime_monitor` with GET is R2**, but a GET can still carry arbitrary headers to any URL.
22. **`stripe/setup_recipient` is R1**, but recipient onboarding may contact the recipient; Stripe does not publish the arguments.

## Consistency
23. **Deletes:** `terraform/delete_team` is R3 while Sentry's deletes are R4. Both are defensible; should the registry pick one rule for "configuration that can be rebuilt by hand"?

## Undo audit (October 2026)

A second audit checked every R1 and R2 entry's undo against four tests: the undo exists, it reverses *this* action rather than a different one, nobody outside the principal has to agree, and what it leaves behind is bounded. It was prompted by an example in this repository that ran an undo in the wrong direction. Changes are in the commit history; the cases worth knowing:

- **Wrong direction.** `git_add` and `git_reset` named each other as their undo, but each loses the previously staged version (now R3). `terraform/create_workspace_tags` overwrites existing values, so removing the tags does not restore them (now R2). `github/update_pull_request_branch` named a revert, which removes the base's changes from the PR instead (now R3). Several compensations would have deleted data that existed before the call (a pending review's other comments, a project item or memory entity that was already there) and now say to undo only what the call created.
- **Undo does not exist.** `sqlite/append_insight` has no removal tool (now R2). `stripe/setup_recipient` named a removal step Stripe does not document (now R2).
- **Undo incomplete.** `git_checkout` silently overwrites ignored files the target branch tracks (now R4). `filesystem/edit_file` re-encodes the file (now R4). Closing a Playwright browser or tab loses session state (now R3).

Open questions from this audit:

24. **CI and webhook side effects.** `create_branch`, label creation, starring and forking fire GitHub Actions events, in someone else's repository for stars and forks. Deleting the branch does not cancel the run. Should triggered automation count against R1?
25. **Pages that act on what they saw.** Removing a Playwright network mock does not undo what the page did with the faked response. In scope, or out like `browser_hover`?
26. **Undo that loses later data.** `supabase/create_branch` is R2, but its undo, `delete_branch`, also destroys anything written to the branch since. Is that still compensation?
27. **Secret scanning.** GitHub scans secret gists and alerts the matching secret's provider. Is that a disclosure that rules out R1 for secret gists?
28. **Size limits on compensation.** `stripe/convert_currency` converts back within daily limits, at a fee both ways. Should conversions above the cap be R3?
29. **Default alert rules.** `sentry/create_project` also creates a client key and, by default, an alert rule. Is R1 honest?
30. **Reads that carry data out.** `fetch` is R0, but a GET with private data in its query string discloses that data to the remote host. SPEC section 9 treats this as an aggregation concern, not a property of `fetch`. Should a URL that contains data the session has read raise the class of that one call?
31. **Rules that depend on a server's defaults.** `github/create_repository` was lowered to R1 when `private` was omitted, because the current server defaults it to true; v0.10.0 sends no default and GitHub creates a public repository. The rule was removed. Should records carry the server versions each rule was verified against, and should a rule that depends on a default never lower a class outside that range?
