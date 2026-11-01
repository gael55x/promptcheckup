# promptcheckup

Prove a prompt change made your AI agent better, with paired replay.

Status: early development. Nothing is released yet. See [DESIGN.md](DESIGN.md) for the v1.0 plan.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install pytest==9.1.1 ruff==0.17.1
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

## License

Apache-2.0. See [LICENSE](LICENSE).
