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
- 主源码最终 Python/TUI 合并回归：52 项全部通过；结合宿主 21 项，共 73 项。集成工作树首次有 2 项因缺少忽略的 ui-tui/dist 产物失败，主源码验证已解决此环境差异。
- 首版沿用后端删除确认与 ask_user，没有增加通用危险工具审批、文件差异面板或终端面板。

模块提交：host 33c910f、python 2b2158f、renderer b464c32。集成修复包括异步会话状态、中文输入法回车、IPC 范围、日志与连接清理。原模块分支保留以供追溯。

## 本机交付

- `ui-desktop/out/RinCode-darwin-arm64/RinCode.app` 已生成并直接启动验证，Electron 44.3.0、Packager 20.3.0。
- 应用包未设置 RINCODE_PYTHON / RINCODE_SOURCE_ROOT 覆盖也能定位主源码 `.venv`，完成真实 ask_user 提问、用户回答和后续回复。
- 打包窗口中切换到第二个临时项目后会话列表为空，隔离正确；关闭后未发现桌面后端进程残留。
- 主源码执行保存的 `npm --prefix ui-desktop run test:e2e` 再次通过，覆盖全新项目自动创建首会话；结果与截图在 `ui-desktop/out/verification/`。
- 本机依赖使用 `npm ci` 独立安装，运行不依赖模块工作树；Python 仍复用本机源码和虚拟环境，非跨机器分发安装包。

## 界面打磨与 README 截图

暖色主题、侧栏、项目选择器、输入框和欢迎页已打磨；完成后的思考内容默认收起。修正 Markdown 列表分段后的编号，真实历史会话验证为 1、2、3。类型检查、构建和 macOS 打包通过，已重新打开应用包确认新版欢迎页。

界面改动由 Antigravity Gemini 在 `codex/desktop-polish` 起草，协调者完成集成检查。`docs/desktop/screenshot.png` 为真实模型读取 README 文档副本后生成回复的实际应用截图，已经加入项目 README 与桌面使用说明。
