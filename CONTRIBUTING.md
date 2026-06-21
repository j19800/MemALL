# Contributing to MemALL

Thanks for your interest in contributing! We're an open-source, community-driven project and welcome contributions of all kinds.

## 🐛 Bug Reports

Open a [GitHub Issue](https://github.com/j19800/MemALL/issues/new) with:
- A clear title and description
- Steps to reproduce
- Expected vs actual behavior
- Your environment (OS, Python version, MemALL version)

## 💡 Feature Requests

Open a [Discussion](https://github.com/j19800/MemALL/discussions) first. We use L4 decisions internally to track feature planning — your proposal will be treated the same way.

## 🔧 Pull Requests

1. Fork the repo
2. Create a feature branch (`git checkout -b feat/your-feature`)
3. Make your changes
4. Run tests: `pytest`
5. Open a PR against `main`

### PR Checklist
- [ ] Tests pass (`pytest`)
- [ ] New tests added if applicable
- [ ] Code follows existing style (we use `black` + `ruff`)
- [ ] Commit messages are clear

## 🧪 Development Setup

```bash
git clone https://github.com/j19800/MemALL
cd memall
pip install -e .[dev]
pytest
```

## 📋 Code of Conduct

Be respectful. We're building something cool — let's keep it fun.
