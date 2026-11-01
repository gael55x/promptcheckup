# Node.js command agent

With Node.js on PATH and promptcheckup installed, run
`promptcheckup doctor --config examples/connect/node_cmd/promptcheckup.toml --no-llm`.
No npm packages are needed. The score function is Python; the agent can use any language.

From this directory, `node agent.mjs < input.json` produces the JSON in `output.json`.
Diagnostics belong on stderr, leaving stdout for exactly one JSON value.

The 20 manual toy cases check the greeting contract. Replace them with representative
tasks before trusting a verdict. See [Connect your agent](../../../docs/connect-your-agent.md).
