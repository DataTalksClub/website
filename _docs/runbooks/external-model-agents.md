# Running Codex and Grok agents

Both CLIs are installed on the workstation and run non-interactively, so work can be
spread across model providers instead of consuming a single provider's quota. They are
driven as ordinary background shell processes, not as Claude Code subagents.

Neither CLI is a dependency of this repository. Nothing in CI uses them, and no target
here invokes them. They are a workstation convenience for delegating analysis and
implementation work.

## Codex

```console
codex exec \
  -c model=gpt-5.6-sol \
  -c model_reasoning_effort=medium \
  --sandbox read-only \
  "your prompt here"
```

- Binary: `~/.nvm/versions/node/<version>/bin/codex` (`@openai/codex`).
- `exec` is the non-interactive subcommand; the interactive TUI is the default and must
  not be used from an agent session because it never exits.
- Model and reasoning effort are set through `-c key=value` overrides rather than
  dedicated flags. `-m/--model` also works for the model.
- `--sandbox read-only` for analysis. Widen it only when the task genuinely needs to
  write, and prefer pointing the run at a scratch worktree over the shared one.
- `--json` emits JSONL events; `-o FILE` writes the final message to a file, which is
  the easiest way to capture a long answer.
- `--skip-git-repo-check` allows running outside a git repository.

## Grok

```console
grok \
  -m grok-4.6 \
  --effort medium \
  --permission-mode auto \
  -p "your prompt here"
```

- Binary: `~/.local/bin/grok`.
- `-p/--single` is the single-turn form: it prints the response to stdout and exits.
  Without it the command opens an interactive TUI that will hang a background job.
- `grok models` lists what the account can reach. As of 2026-09-02 that is `grok-4.6`
  (default) and `grok-4.5`.
- `--reasoning-effort` (alias `--effort`) sets reasoning depth.
- `--output-format` and `--json-schema` are available when structured output is wanted.

## Using them from an agent session

- Always run with a timeout and `run_in_background`, then read the output file. A
  foreground call will block the session for as long as the model thinks.
- Ask for the deliverable to be written to a file under `.tmp/` and for only a short
  summary on stdout. Long answers on stdout are expensive to read back.
- Give the same quality of brief a Claude subagent would get: where the code is, what is
  already known, what is out of scope, and what other agents own. These CLIs have no
  access to the conversation and will otherwise rediscover or duplicate work.
- State the repository conventions explicitly. They have not read `AGENTS.md` unless the
  prompt tells them to.

## Constraints that still apply

Work produced by these agents is subject to the same rules as anything else:

- Public URLs must not change. `_docs/compatibility/generated-path-baseline.jsonl` is the
  contract.
- Content-authority digests in `content_sync/dtc_content/contract.py` must not be
  re-pinned.
- Registration data and other PII must stay out of git, logs, and reports.
- Commits in a shared worktree must use explicit paths. Never `git commit -a` or
  `git add -A` while other agents hold staged work in the same index.
