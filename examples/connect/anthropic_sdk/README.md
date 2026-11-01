# Anthropic SDK agent

Use this adapter in an application that already has the Anthropic Python SDK installed.
`Anthropic()` uses the SDK's normal `ANTHROPIC_API_KEY` environment variable.

From this directory run `promptcheckup doctor`. It makes two agent requests and
one judge request. `--no-llm` skips only the judge; the agent still calls Anthropic.
The judge uses Anthropic's OpenAI-compatible endpoint. The three manual sample
cases are for checking the wiring, not a trustworthy verdict.

Tests compile the adapter and validate the config without importing the SDK or
making provider requests. See [Connect your agent](../../../docs/connect-your-agent.md).
