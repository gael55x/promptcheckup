After editing prompts, run `promptcheckup doctor` and fix every failure.
Resolve or explain warnings, especially an agent that ignores the `prompts` argument.
Then run `promptcheckup gate --old-ref origin/main --yes --json result.json`.
Report the gate report's first line verbatim and its exit code: 0 better, 1 worse,
2 not sure yet, 3 configuration/runtime error. Never describe "not sure yet" as a pass.
Keep the old and new prompts on the same cases. Do not merge automatically.
