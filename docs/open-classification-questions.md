# Open classification questions

These came out of the first independent audit of the registry (October 2026). Each is a genuine judgment call where reasonable people could land on different classes. They are good places to start contributing: pick one, argue it in an issue, and propose a change.

The must-fix and should-fix items from that audit have already been applied.

## Cost and billing
1. **Brave Search and Google Maps tools are R0**, but each query is billed. Does a per-call cost to the principal make a read R2?
2. **`supabase/confirm_cost` is R0.** It only returns a token, but the agent can mint its own approval for a billable create. Is that a class question or a policy question?

## Side effects of "safe" actions
3. **`git/git_commit` and `git/git_checkout` are R1**, but git runs repository hooks (`pre-commit`, `post-checkout`) — arbitrary code from the repository. Should hook-running tools be R4 like other code execution, or is code the principal owns different?
4. **`git/git_add` is R1**, but re-staging a path discards the previously staged version (recoverable only with `git fsck`).
5. **`playwright/browser_hover` is R0**, but pages can act on mouse-over.
6. **`github/create_branch` is R1**, but pushes can trigger workflows — and running a workflow directly (`actions_run_trigger`) is R4.
7. **`filesystem/edit_file` is R1** because it returns a diff. Restoring depends on the agent keeping that diff. Should restorability that depends on the caller count?

## What counts as public
8. **Secret gists (`github/create_gist` without `public`) are R1**, but anyone with the URL can read one.
9. **`github/star_repository` is R1**, but stars are publicly visible.
10. **`github/fork_repository` is R1**, but forking into an organization can expose content to people who could not see it before.
11. **Text fields in GitHub Projects** are lowered to R2 on update, but in a public project they are published content.
12. **`sentry/link_issue` is R1**, but some integrations post into an external system.

## Credentials and safety controls
13. **`sentry/create_project` is R1**, but it provisions a client key; `create_dsn` is R3.
14. **`terraform/update_workspace` is R2**, but it can turn on auto-apply — changing a safety control.
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
