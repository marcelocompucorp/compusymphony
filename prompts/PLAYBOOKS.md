# Playbook index

Pointers into `dev-ai-playbooks/.ai/` and `dev-ai-playbooks/.claude/commands/` — both reachable from the workspace via the `./.playbooks/` symlink. **Read on demand, not all at once.**

## Always read before writing code

- `./.playbooks/.ai/shared-development-guide.md` — Compucorp code standards: security, performance, logging, commit conventions, branch naming. **Commit prefix exception:** use `{{ issue.identifier }}:`, not `COMCL-###`. Everything else in §5 (under 72 chars, present tense, no AI co-author trailer) DOES apply.

## Always read before writing tests

- `./.playbooks/.ai/unit-testing-guide.md` — Compucorp unit testing conventions (PHPUnit / Codeception / Drush patterns).

## Read when relevant

- `./.playbooks/.ai/civicrm.md` — CiviCRM architecture, APIv4 patterns, hooks, common pitfalls.
- `./.playbooks/.ai/extension.md` — CiviCRM extension structure, `info.xml`, hooks registration, packaging.
- `./.playbooks/.ai/ai-code-review.md` — the rubric used by the `/review` slash command.

## Slash commands (defined in `./.playbooks/.claude/commands/`)

- `/review` — multi-aspect code review with severity ranking (BLOCKER / WARNING / SUGGESTION). Run it before pushing.
- `/pre-commit` — last-mile checks before `git commit`.

## When in doubt about a path

If the playbook you'd expect to find is missing, **don't fabricate one** — note it in the PR body under `## Notes` (e.g., "Could not find a Compucorp playbook for X; followed the existing repo pattern in `path/to/example.php`.") and proceed with the closest analog from the repo itself.
