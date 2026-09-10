# RinCode

通过自然语言指令完成编码与调试的 Coding Agent。RinCode 可以检索项目代码、修改文件、执行命令和运行测试，并根据工具反馈继续处理任务。

- **工具协作**：文件、Shell、Web 与 MCP 工具共用执行接口，支持子 Agent 委托、参数校验、超时与错误反馈。
- **上下文管理**：按 Token 预算装配任务约束和相关历史，保留工具调用与结果的对应关系，持久化会话。
- **任务执行**：按会话串行运行，隔离前后台任务，支持取消、终态处理与 Trace 诊断。
- **RinBench**：以可检查的文件产物、MCP 回执及独立回归测试验收任务，记录成功率、Token 用量与耗时。

## 快速开始

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

源码仓库：[2568xt/RinCode](https://github.com/2568xt/RinCode)。在源码根目录执行安装器，它会构建所需的 TUI 产物并注册 `rincode` 命令：

```bash
./install.sh
rincode onboard --skip-memory
rincode run -m "阅读这个项目，解释任务执行流程"
```

按引导配置自己的模型服务与 API Key。项目使用 `rincode` 命令、Python 包名及配置名，展示名称为 RinCode。首次引导及实际 Agent 任务会调用模型服务。 命名与接口变化见 [迁移说明](docs/onboarding/rincode-migration.md)。

安装后运行 `rincode` 进入交互式 TUI。TUI 使用 Node.js 22，安装器可在缺少适用版本时安装私有运行时。Windows 在源码目录执行 `./install.ps1`，然后使用同样的 `rincode` 命令。

## 评估与验证

RinBench 是评测系统的展示名称，Python 模块为 `benchmarks.rincodebench`，代码位于 `benchmarks/rincodebench`，包含上下文、工具/MCP 和记忆相关任务。基于产物和执行证据评分，模型的口头完成声明不作为通过依据。

```bash
# 不调用模型的评估冒烟检查
uv run python -m benchmarks.rincodebench --mode smoke

# 任务生命周期与代码修复验收的回归检查
uv run pytest -q tests/test_spine_scheduler_lane.py tests/test_maintenance_acceptance.py
```

代码修复验收在独立副本中运行修复前后的测试，检查受保护文件和允许变更范围。小型修复任务夹具位于 `benchmarks/repair_business`；CLI 验收入口为 `rincode maintain accept --help`。

真实模型评估需要单独配置凭据与预算，会产生 API 费用。运行日志、账号配置和原始会话不随源码发布。详细评估说明见 [RinBench](benchmarks/rincodebench/README.md)。

## 项目结构

| 路径 | 内容 |
| --- | --- |
| `rincode/agent` | Agent 循环与工具 |
| `rincode/context_engine` | 上下文装配 |
| `rincode/spine` | 调度与任务生命周期 |
| `rincode/providers` | 模型服务适配 |
| `rincode/maintenance` | 代码修复验收 |
| `benchmarks` | 评估任务与执行器 |
| `tests` | 回归测试 |
| `ui-tui` | 终端界面 |

## 许可证与来源

采用 Apache-2.0，详见 [LICENSE](LICENSE)。上游作者署名、本仓库维护归属及第三方许可证保留于 [NOTICES.md](NOTICES.md) 和 [LICENSES](LICENSES/)。
