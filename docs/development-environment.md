# 本地开发环境

## Python 与 Conda 环境

项目要求 Python `>=3.10`。后续开发、同步和测试统一使用以下 Conda 环境，不使用仓库内 `.venv`：

```text
C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe
```

PowerShell 每次运行 Python 或 pytest 前定义解释器变量：

```powershell
$env:ARR_PYTHON = 'C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe'
& $env:ARR_PYTHON --version
```

首次安装/更新依赖：

```powershell
$env:ARR_PYTHON = 'C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe'
& $env:ARR_PYTHON -m pip install --upgrade pip
& $env:ARR_PYTHON -m pip install -r requirements-dev.txt
```

## 自动开发准备

`DEV_SPEC.md` 是自动开发的单一规格来源。运行同步脚本后，章节将导出到 `.github/skills/auto-coder/references/`；自动开发器根据 `06-schedule.md` 中的 `[ ]`、`[~]`、`[x]` 标记选择任务。

```powershell
$env:ARR_PYTHON = 'C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe'
& $env:ARR_PYTHON .github\skills\auto-coder\scripts\sync_spec.py --force
Get-Content .github\skills\auto-coder\references\06-schedule.md
```

运行测试：

```powershell
$env:ARR_PYTHON = 'C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe'
& $env:ARR_PYTHON -m pytest -q
```

ARR 的所有单元、集成与 E2E 测试必须使用 Mock/Fake 后端，不能执行真实 shell、写工作区或联网。
