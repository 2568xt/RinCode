# 桌面首版验证记录

## 模块来源

由协调者固定共享接口，Antigravity CLI 1.2.0 使用账户登录调用 Gemini 3.8 Flash High，在独立分支与工作树实现：

| 模块 | 分支 | Antigravity 会话 |
| --- | --- | --- |
| Electron 宿主 | codex/desktop-host | 1e968af8-e9a8-47fb-9054-5c3449926edf |
| React 界面 | codex/desktop-renderer | e2276242-ae80-4838-81df-35cb8df1818e |
| Python 启动器 | codex/desktop-python | 84a679d5-8629-4de1-9ddd-51cea916b191 |

CLI 来自官方发布清单并校验 SHA512。未使用独立 Gemini CLI 或 Gemini API key 模式。无交互 shell 请求被软拒绝，因此由 Gemini 使用工作区文件工具实现，协调者执行测试、提交和集成；不能仅凭 CLI SUCCESS 判定模块完成。

## 基线

2026-09-10 原始提交 a8b84fd：38 项 TUI 定向回归通过，覆盖 socket、确认、流订阅、CLI 启动。首次沙箱运行有 5 项因禁止 socket bind 失败；允许本机 socket 后全部通过。

## 验收结果（2026-09-10）

- Electron 宿主：21 项测试通过，包含失败启动、已退出进程、并发连接、断连、超时及权限方法限制。
- Python 桌面启动器：14 项测试通过，包含认证、会话、stdin EOF、SIGTERM、错误 token、socket 关闭和配置 workspace 覆盖隔离。
- TypeScript 类型检查与 esbuild 构建通过。
- 真实后端模型：当前配置 deepseek/deepseek-v4-flash，成功调用 read_file 读取独立测试项目 README 标记 RINCODE_GUI_REAL_TOOL_20260910，收到 message.complete。
- 真实 Electron 窗口：首条消息读取工具、连续第二轮、新会话、跨会话历史恢复、删除确认拒绝与同意、停止生成通过；没有 renderer console/page error。截图已人工查看。
- 集成工作树的 TUI 回归有 2 项因未复制忽略的 ui-tui/dist 产物失败，50 项通过；最终在含原有产物的主源码目录再验证。
- 首版沿用后端删除确认与 ask_user，没有增加通用危险工具审批、文件差异面板或终端面板。

模块提交：host 33c910f、python 2b2158f、renderer b464c32。集成修复包括异步会话状态、中文输入法回车、IPC 范围、日志与连接清理。原模块分支保留以供追溯。
