# Free, local-first testing & CI for Python computer-vision projects (Jetson + Windows)

> Goal: verify that every change does not break anything, paying nothing, mostly testing locally, using a development Jetson as the test server.

## 1. The idea in one page

Make **one command** that runs your tests, then trigger it automatically at progressively later moments.

| Stage | Trigger | Runs where | Checks | Cost |
|---|---|---|---|---|
| A | `git commit` | your PC | formatting + lint (seconds) | free |
| B | `git push` | your PC | fast tests (< 1 min) | free |
| C | on demand / after push | Jetson (test server) | full tests incl. GPU | free |
| D (optional) | after push | GitHub's servers | CPU-only tests on a clean machine | free tier |

**Why layers?** Fast checks run often because you will tolerate them. Slow, thorough checks run less often but still automatically. A check you must remember to run eventually gets skipped.

### Glossary

| Term | Meaning |
|---|---|
| **CI** (Continuous Integration) | Automatically running tests on every change. |
| **pytest** | Python test runner. Finds `test_*.py` files and runs the functions inside. |
| **Linter** | Tool that finds suspicious code without running it (unused variables, undefined names). |
| **Ruff** | A very fast linter + formatter for Python. |
| **Git hook** | Script Git runs at certain moments (`pre-commit`, `pre-push`). |
| **pre-commit (framework)** | Tool that installs and manages hooks from a versioned config file. |
| **Runner** | Machine/program that executes CI jobs. |
| **Self-hosted runner** | A runner on your own hardware (e.g. the Jetson). |
| **Workflow** | GitHub Actions YAML file describing what to run and when. |
| **Matrix** | Running the same job on several OS / Python versions. |
| **Fixture** | pytest helper that prepares data/objects for tests. |
| **Marker** | Label on a test (`@pytest.mark.gpu`) used to select or skip tests. |
| **Flaky test** | Test that sometimes passes and sometimes fails with no code change. |
| **Environment-leaking test** | Test that changes global state (env var, cwd, file) and breaks other tests. |
| **Branch protection** | GitHub rule that blocks merging to `main` unless checks pass. |

---

## 2. Recommended project layout (the shared template)

```
my-project/
├── .github/workflows/ci.yml        # Stage D (optional)
├── .claude/
│   ├── settings.json               # lets Claude Code run tests without asking
│   └── skills/testing/SKILL.md     # testing rules for Claude (see section 9)
├── .pre-commit-config.yaml         # Stage A + B hooks
├── CLAUDE.md                       # project instructions for Claude Code
├── Makefile                        # the "one command" entry points
├── pyproject.toml                  # pytest + ruff configuration
├── requirements.txt
├── requirements-dev.txt            # pytest, ruff, pre-commit, ...
├── scripts/remote_test.sh          # Stage C (run tests on the Jetson)
├── src/my_project/
│   └── pipeline.py
└── tests/
    ├── conftest.py                 # shared fixtures, seeding, GPU auto-skip
    ├── data/                       # tiny fixture images + expected outputs
    ├── test_geometry.py            # unit tests
    └── test_pipeline.py            # pipeline tests
```

What each file does:

| File | Purpose |
|---|---|
| `pyproject.toml` | Central config for pytest and Ruff. |
| `Makefile` | Short commands (`make test-fast`) reused by hooks, the Jetson and CI. Change test behavior in one place. |
| `requirements-dev.txt` | Tools used only for development/testing. |
| `tests/conftest.py` | Fixtures and rules shared by all tests. |
| `tests/data/` | Small committed inputs (a few KB each) so tests are reproducible. |
| `.pre-commit-config.yaml` | Declares which hooks run at commit and push. |
| `scripts/remote_test.sh` | Copies the project to the Jetson and runs the full suite there. |
| `.github/workflows/ci.yml` | Instructions for GitHub's servers. |
| `.claude/settings.json` | Permissions so Claude Code can run tests/lint without prompting. |
| `.claude/skills/testing/SKILL.md` | Rules that tell Claude how to write tests and what to do when they fail. |
| `CLAUDE.md` | Short project facts Claude reads every session. |

---

## 3. Foundation: tests with pytest

CI only *automates running tests*. Without good tests it automates nothing, so this is the most important part.

### Three kinds of tests for computer vision

1. **Unit tests**: pure logic (geometry, coordinate transforms, parsing). Fast, no images.
2. **Pipeline tests**: feed a small fixture image through your processing and check the output.
3. **Hardware tests**: need GPU/CUDA/TensorRT or a camera. Marked `gpu` and run only on the Jetson.

### `pyproject.toml`

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"                      # show a summary of skipped/failed tests
markers = [
  "gpu: needs CUDA/TensorRT (Jetson only)",
  "slow: takes more than a few seconds",
]

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "B"]        # pycodestyle, pyflakes, import order, bugbear
```

What it does: tells pytest where tests live and which markers exist (an unknown marker would otherwise be a silent typo). Tells Ruff which rule families to check.

### `requirements-dev.txt`

```
pytest
pytest-randomly      # shuffles test order to expose environment-leaking tests
pytest-cov           # coverage (optional)
pytest-xdist         # parallel runs (optional, only if the suite gets slow)
ruff
pre-commit
```

### `tests/conftest.py`

```python
import random
from pathlib import Path

import numpy as np
import pytest

DATA = Path(__file__).parent / "data"


@pytest.fixture(autouse=True)
def _seed():
    """Make every test deterministic."""
    random.seed(0)
    np.random.seed(0)


@pytest.fixture
def sample_image():
    import cv2
    img = cv2.imread(str(DATA / "sample.png"))
    assert img is not None, "tests/data/sample.png is missing"
    return img


def _cuda_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    """Automatically skip GPU tests on machines without CUDA."""
    if _cuda_available():
        return
    skip_gpu = pytest.mark.skip(reason="no CUDA on this machine")
    for item in items:
        if "gpu" in item.keywords:
            item.add_marker(skip_gpu)
```

What it does: `_seed` makes random behavior repeatable. `sample_image` loads a fixture once for any test that asks for it. The last function makes GPU tests skip themselves on your PC and run on the Jetson, so the same test suite works everywhere.

### `tests/test_geometry.py` (unit test)

```python
import pytest
from my_project.geometry import pixels_to_mm


def test_pixels_to_mm_basic():
    assert pixels_to_mm(100, px_per_mm=50) == pytest.approx(2.0)


def test_pixels_to_mm_rejects_zero_scale():
    with pytest.raises(ValueError):
        pixels_to_mm(100, px_per_mm=0)
```

### `tests/test_pipeline.py` (pipeline + GPU test)

```python
import numpy as np
import pytest
from my_project.pipeline import process


def test_pipeline_smoke(sample_image):
    """Runs end to end on one image without crashing. Write this one first."""
    result = process(sample_image)
    assert result is not None


def test_pipeline_matches_expected(sample_image):
    result = process(sample_image)
    expected = np.load("tests/data/expected.npy")
    # Tolerance, never ==. Float results differ slightly between CPUs/CUDA/OpenCV builds.
    np.testing.assert_allclose(result, expected, atol=1e-3)


@pytest.mark.gpu
def test_detector_runs_on_gpu(sample_image):
    from my_project.detector import detect
    boxes = detect(sample_image, device="cuda")
    assert len(boxes) >= 1
```

### Why tolerances matter

Floating-point results differ slightly between machines. Exact equality creates "flaky" tests that fail for no reason. Use `np.testing.assert_allclose` or `pytest.approx`.

### Fast vs full runs

```bash
pytest -m "not gpu and not slow"   # fast: your PC, before push
pytest                              # full: Jetson
```

---

## 4. One command for everything: `Makefile`

**Important:** indent recipe lines with a real TAB character, not spaces.

```make
.PHONY: lint test-fast test-all

lint:
	ruff check .
	ruff format --check .

test-fast:
	pytest -m "not gpu and not slow"

test-all:
	pytest
```

Why: hooks, the Jetson, CI and Claude Code all call `make test-fast` / `make test-all`. If you change how tests run, you change one file. On Windows without `make`, put the same commands in `scripts/test.ps1`, or run the `pytest` command directly.

---

## 5. Stage A: Ruff + the `pre-commit` framework

**Ruff** checks and formats code in well under a second: unused variables, undefined names, bad imports, inconsistent style.

**Git hooks** live in `.git/hooks/`, which is *not* shared when you clone. Every project and machine would need them reinstalled by hand. The **pre-commit framework** fixes this: hooks are declared in a versioned file, and one command installs them.

### `.pre-commit-config.yaml` (commit-time hooks)

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9            # check the latest release and update
    hooks:
      - id: ruff
        args: [--fix]      # auto-fix what it can
      - id: ruff-format
```

Install once per clone:

```bash
pip install pre-commit
pre-commit install
```

What it does: on every `git commit`, Ruff runs on the changed files. If it fixes or finds something, the commit is stopped so you can review and commit again.

Why it matters: trivial bugs and style noise never reach the repository.

---

## 6. Stage B: the pre-push hook (blocks a push if fast tests fail)

Add a second block to `.pre-commit-config.yaml`, under `repos:`:

```yaml
  - repo: local
    hooks:
      - id: pytest-fast
        name: pytest (fast)
        entry: make test-fast
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-push]
```

Install both hook types:

```bash
pre-commit install --hook-type pre-commit --hook-type pre-push
```

Limitations:

- `git push --no-verify` skips it. It protects against accidents, not against yourself.
- It only runs on the machine where it is installed.
- It must stay fast (about a minute at most), or you will start bypassing it. Slow tests belong to stage C.

---

## 7. Stage C: the Jetson as a test server

### Option 1 (recommended): plain script over SSH

Free, no extra software, no account. Copies your working tree to the Jetson and runs the full suite there.

#### `scripts/remote_test.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

JETSON="user@jetson-dev"                 # your Jetson user@host
DIR="~/ci/$(basename "$PWD")"

rsync -az --delete \
  --exclude .git --exclude .venv --exclude __pycache__ \
  ./ "$JETSON:$DIR/"

ssh "$JETSON" "cd $DIR && source .venv/bin/activate && make test-all"
```

Make it executable: `chmod +x scripts/remote_test.sh`. On Windows, run it from WSL or Git Bash (needs `rsync`), or use `scp` instead.

#### One-time Jetson preparation

```bash
# On your PC: passwordless SSH
ssh-keygen -t ed25519
ssh-copy-id user@jetson-dev

# On the Jetson: a persistent virtual environment per project
mkdir -p ~/ci/my-project && cd ~/ci/my-project
python3 -m venv --system-site-packages .venv   # often needed so JetPack's OpenCV/CUDA/PyTorch are visible
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Note: `rsync --delete` only removes files that are not excluded, so the `.venv` survives between runs. Heavy dependencies are installed once.

Why it matters: this runs your tests on the exact hardware and software that production uses, including the GPU.

Optional: also call it from the pre-push hook by adding another `local` hook with `entry: scripts/remote_test.sh`. Do this only if the full suite is fast enough.

### Option 2: GitHub Actions self-hosted runner on the Jetson

A small program on the Jetson connects outbound to GitHub (no port forwarding) and executes workflow jobs. Results show as green/red checks on GitHub.

Setup outline (the exact commands are shown in your repo under *Settings → Actions → Runners → New self-hosted runner*; choose **Linux / ARM64**):

```bash
./config.sh --url https://github.com/OWNER/REPO --token <TOKEN> --labels jetson,gpu
sudo ./svc.sh install <non-root-user>
sudo ./svc.sh start
```

Workflow job that uses it:

```yaml
jobs:
  gpu-tests:
    runs-on: [self-hosted, jetson]
    steps:
      - uses: actions/checkout@v4
      - run: |
          python3 -m venv --system-site-packages .venv
          . .venv/bin/activate
          pip install -r requirements-dev.txt
          make test-all
```

Cautions:

- **Cost:** GitHub announced a per-minute fee for self-hosted runners starting March 2026 and then reportedly backed off. Check GitHub's current billing page before relying on this if you want to pay nothing.
- **Security:** never use a self-hosted runner on a *public* repo that accepts pull requests from strangers. They could run code on your Jetson.
- Use a dedicated non-root user.

### Option 3: Forgejo/Gitea or Jenkins on the Jetson

Fully free and local, but you must maintain a CI server. Overkill for solo projects.

---

## 8. Stage D (optional): GitHub Actions on GitHub's servers

Runs on a **clean** machine, so it catches "works on my PC" problems such as a dependency missing from `requirements.txt`. It cannot test GPU code (hosted runners have no GPU).

### `.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    env:
      QT_QPA_PLATFORM: offscreen      # lets Qt code run without a display
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.10"      # match the Python on your Jetson
          cache: pip                  # caches downloaded packages between runs
      - run: pip install -r requirements-dev.txt
      - run: make lint
      - run: make test-fast
```

Line by line:

- `on:` triggers on each push and pull request.
- `runs-on: ubuntu-latest` is a GitHub-provided Linux VM.
- `QT_QPA_PLATFORM=offscreen` lets Qt render without a screen.
- `setup-python` installs Python; `cache: pip` speeds up later runs.
- The last two lines call the same Makefile as everywhere else.

### Windows (only if you ship to Windows)

```yaml
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
    runs-on: ${{ matrix.os }}
```

Windows minutes count at a multiplier on private repos. A common compromise is Windows only on pull requests to `main`. Windows has no `make` by default, so call `pytest` directly in the Windows step.

### Cost and branch protection (verify current numbers)

- As far as I know, public repos are free. Private repos on the Free plan get a monthly allowance of free minutes (about 2,000 Linux minutes). Check GitHub's billing page.
- **Branch protection** (require checks before merging to `main`) may need a paid plan on private repos. If so, your pre-push hook and discipline replace it.

---

## 9. Teaching Claude how to test: skill, rules and permissions

### Are there public skills for testing?

Yes, there are community skills for pytest and coverage, installed with `npx skills add ...` into `.claude/skills`. Examples I found:

| Skill | Source | What it covers |
|---|---|---|
| `pytest` | `bobmatnyc/claude-mpm-skills` | General pytest guidance |
| `python-testing` | `laurigates/claude-plugins` | Fixtures, parametrization, coverage, mocking |
| `pytest-coverage` | `github/awesome-copilot` | Running coverage and finding untested lines |
| `skill-testing` | `crashcartlabs/skill-testing` | Linting/testing *skills themselves* |

I have not audited these. They are third-party instructions that Claude will follow, so **read each `SKILL.md` before installing**. Most are generic. None will know your specific rules (tolerances, GPU markers, what to do when a test fails), so the best option is a short skill of your own, below. Put it in the shared template.

### `.claude/skills/testing/SKILL.md`

```markdown
---
name: testing
description: Use when writing, running, fixing or reviewing tests, or when a test or lint check fails in this project. Defines how to write tests, how to run them, and how to behave when they fail.
---

# Testing rules

## Commands
- `make lint`: Ruff check and format check.
- `make test-fast`: tests without GPU or slow markers. Run before saying a task is done.
- `make test-all`: full suite (GPU tests run only on the Jetson).
- Run `make lint` and `make test-fast` after every code change. Never claim work is finished without running them, and report what you actually ran.

## Writing tests
- Location: `tests/`, files `test_<module>.py`, functions `test_<behavior>`.
- One behavior per test. Structure: arrange, act, assert.
- Every bug fix gets a regression test. The test must fail without the fix and pass with it.
- Compare floats and images with tolerance (`pytest.approx`, `np.testing.assert_allclose`). Never use `==` on floats or arrays.
- Fixtures: tiny inputs in `tests/data/` (a few KB). Never commit large datasets.
- Use `tmp_path` for files and `monkeypatch` for env vars, cwd and globals. Never leave global state changed.
- Tests must not need network, camera, display or the GPU, unless marked `@pytest.mark.gpu`.
- Tests must be deterministic and independent of order. Seed randomness (conftest does it).
- Test behavior and results, not internal implementation details.

## When a test fails
1. Read the full failure output. Do not guess.
2. Reproduce it with the single failing test: `pytest tests/test_x.py::test_name -x`.
3. Decide: is the **code** wrong, or is the **test** wrong (outdated expectation, bad fixture)?
4. Fix the root cause in the code, or fix the test and explain why the old expectation was wrong.
5. Re-run the failing test, then `make test-fast`. Report the result.
6. If you cannot find the cause after a reasonable attempt, stop and report what you tried. Do not hide the failure.

## Never do this to make tests pass
- Do not delete, skip (`skip`, `xfail`) or comment out a failing test.
- Do not loosen tolerances, weaken assertions or catch exceptions broadly to get green.
- Do not regenerate expected outputs (`expected.npy`, golden images) unless the new behavior is intended. Show the user the difference and ask first.
- Do not use `git push --no-verify`, disable hooks, or edit CI to ignore failures.
- Do not add `time.sleep` or automatic reruns (`pytest-rerunfailures`) to hide flakiness.
- Do not change production code only to satisfy a badly written test without saying so.

## Flaky or order-dependent tests
- A test that passes sometimes is a bug. Run it 20 times: `pytest tests/test_x.py::test_name --count=20` (needs pytest-repeat) or in a loop.
- Common causes: unseeded randomness, exact float comparison, shared global state, files left in the working directory, test order.
- Find and fix the cause. Run the suite in random order (pytest-randomly) to check for leaks.

## Reporting
At the end, say: what changed, which commands were run, pass/fail counts, anything skipped and why, and any remaining risk.
```

### `CLAUDE.md` (short, read every session)

```markdown
# Project notes
- Python 3.10, runs on Jetson (JetPack) and sometimes Windows.
- Tests: `make test-fast` (before every push), `make test-all` (Jetson, includes GPU).
- Lint/format: Ruff via pre-commit. Do not bypass hooks.
- Follow the `testing` skill for all test work.
```

### `.claude/settings.json` (run tests without asking, keep guardrails)

```json
{
  "permissions": {
    "defaultMode": "default",
    "allow": [
      "Read(**)",
      "Glob(**)",
      "Grep(**)",
      "Bash(make lint)",
      "Bash(make test-fast)",
      "Bash(make test-all)",
      "Bash(pytest*)",
      "Bash(ruff*)",
      "Bash(git status)",
      "Bash(git diff*)",
      "Bash(git log*)"
    ],
    "ask": [
      "Bash(git commit*)",
      "Bash(git push*)",
      "Bash(scripts/remote_test.sh*)"
    ],
    "deny": [
      "Bash(rm -rf*)",
      "Bash(curl*)",
      "Read(./.env)",
      "Read(./.env.*)",
      "Read(./secrets/**)"
    ]
  }
}
```

What it does: reading code and running tests/lint never prompts. Commits, pushes and remote runs ask first. Dangerous commands and secrets are blocked. Rules are prefix patterns, so keep them narrow. Adjust to your actual commands.

---

## 10. Test-quality problems to prevent

| Problem | Cause | Prevention |
|---|---|---|
| Flaky tests | Unseeded randomness, timing, exact float comparison | Seed, tolerances, no `sleep`. Reruns are a last resort. |
| Environment leaks | Tests change env vars, cwd, globals, files | `monkeypatch`, `tmp_path`, `pytest-randomly` to shuffle order |
| Slow suite | Big images, many tests | Small fixtures, `slow` marker, `pytest -n auto` (xdist) |
| Untested code | Gaps | `pytest --cov=src` to *find* gaps (not to chase a percentage) |
| Type bugs | Wrong argument types | mypy/pyright once code is annotated |

---

## 11. Rollout plan

1. Add `pyproject.toml` pytest/Ruff config, `Makefile`, `requirements-dev.txt` to the template.
2. Add `tests/conftest.py` and one **smoke test** ("pipeline runs on one image without crashing"). Most protection for least effort.
3. Add `.pre-commit-config.yaml` and run `pre-commit install --hook-type pre-commit --hook-type pre-push`.
4. Add the `testing` skill, `CLAUDE.md` and `.claude/settings.json`.
5. Set up the Jetson venv and `scripts/remote_test.sh`.
6. Optionally add the GitHub workflow.
7. From then on: every bug fix gets a regression test.

---

## 12. Tools on your original list: keep or skip

| Tool | Verdict |
|---|---|
| GitHub Actions | Optional stage D |
| Self-hosted runner / Jetson runner | Optional, check billing |
| Git hooks + pre-commit framework | **Use** |
| Ruff | **Use** |
| pytest, pytest-randomly | **Use** |
| pytest-cov | Use occasionally |
| pytest-xdist | Later, if slow |
| pytest-rerunfailures | Avoid (hides bugs) |
| mypy / pyright | Later |
| Qt offscreen | Use if you test Qt code |
| Branch protection | If your plan allows it |
| Docker | Later, if environments become hard to reproduce |
| tox / nox | Skip (single Python version) |
| `act` | Only to debug workflow files |
| Jenkins, TeamCity, GitLab CI, Drone, Woodpecker, Forgejo/Gitea Actions, Buildkite, CircleCI, Azure Pipelines | Skip for now (same job as GitHub Actions with more setup) |

---

*Notes: tool versions (Ruff `rev`, `actions/*` versions) and GitHub pricing change over time. Check them when you set this up.*
