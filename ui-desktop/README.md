# RinCode Desktop

独立 Electron 桌面首版：项目目录选择、会话侧栏、聊天流、工具执行记录、停止生成、删除会话确认，以及 Agent 的交互提问。连接现有 RinCode Python 后端和模型配置。

## 本机运行

需要 Node.js 22.12+，以及已安装 RinCode 依赖的 Python 环境。默认使用源码根目录 `.venv/bin/python`，可通过 `RINCODE_PYTHON` 指定现有解释器。

```sh
npm --prefix ui-desktop ci
npm --prefix ui-desktop run build
npm --prefix ui-desktop start
```

首次依赖安装会下载 Electron 运行时。点击“添加本地项目”选择工作目录，直接发送消息即可创建会话。任务运行时停止按钮或 Esc 可以取消；中文输入法选词回车不会发送。

## macOS 应用

```sh
npm --prefix ui-desktop run package:mac
open ui-desktop/out/RinCode-darwin-arm64/RinCode.app
```

打包结果位于 `ui-desktop/out/RinCode-darwin-<架构>/RinCode.app`。这是本机首版，应用包记录本机源码目录并复用其中的 Python 环境，尚未将 Python 和依赖封装为可分发安装包；移动源码后应重新打包。未配置新的模型凭据，也未替换现有 TUI。

## 验证

```sh
npm --prefix ui-desktop run type-check
npm --prefix ui-desktop test
.venv/bin/python -m pytest tests/test_desktop_server.py -q
# 可选：使用当前真实模型，创建临时项目，执行少量模型请求
npm --prefix ui-desktop run test:e2e
```

E2E 使用真实 Electron、Python、会话与模型，仅将原生目录选择器指向新建临时项目；结果和截图在 `ui-desktop/out/verification/`。单元测试中的模拟传输不作为模型端到端证据。

## 实现边界

主进程持有随机认证 token，通过本机 TCP 连接 Python；渲染层只接触受限 IPC，不能读凭据或直接运行 Node。项目切换会关闭前一个运行时，项目执行目录与会话存储保持一致。桌面路径适配仅在独立后端进程内生效，不改用户配置，也不等同于文件系统沙箱。

操作确认复用真实后端：删除会话需要确认，`ask_user` 展示问题并返回答案。首版没有额外实现通用危险工具审批、终端面板或文件差异查看器。

模块由 Antigravity CLI 的 Gemini 在三个独立分支实现，协调者审查、修复并合并。详细记录见 `docs/desktop/verification.md`。
