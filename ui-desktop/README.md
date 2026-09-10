# RinCode 桌面宿主模块 (Desktop Host Module)

RinCode 桌面宿主是基于 Electron 构建的轻量、安全的原生 macOS 宿主应用程序。宿主负责管理 Python 后端子进程生命周期、提供持久化项目选择、建立经过本地随机令牌鉴权的 TCP 回环 JSON-RPC 2.0 通信，并通过上下文隔离（Context Isolation）将冻结的桌面桥接接口（`bridge.d.ts`）安全暴露给 React 渲染层。

---

## 架构与安全模型

```
┌─────────────────────────────────────────────────────────────┐
│                       Electron 宿主进程                      │
│                                                             │
│  ┌──────────────────────┐        ┌────────────────────────┐ │
│  │   BrowserWindow      │  IPC   │     Main (main.cjs)    │ │
│  │ contextIsolation=true├───────►│  • IPC 调用发送源校验    │ │
│  │ nodeIntegration=false│        │  • 方法白名单校验       │ │
│  └──────────┬───────────┘        │  • 项目持久化 (Store)   │ │
│             │                    └───────────┬────────────┘ │
│             ▼                                │              │
│       preload.cjs                            ▼              │
│    (window.rincode)                  backend-manager.cjs    │
│                                              │              │
└──────────────────────────────────────────────┼──────────────┘
                                               │ TCP 回环 (127.0.0.1:port)
                                               │ 首行鉴权令牌 (Token)
                                               │ 换行分隔 JSON-RPC 2.0
                                               ▼
                                   ┌────────────────────────┐
                                   │ Python 运行时进程       │
                                   │ python -m rincode.cli. │
                                   │       desktop_server   │
                                   │ cwd = 当前项目目录      │
                                   └────────────────────────┘
```

### 安全规范与防御措施
- **上下文隔离与无 Node 注入**：`contextIsolation: true`，`nodeIntegration: false`，`sandbox: true`。渲染层无法直接访问操作系统 API。
- **IPC 调用源校验**：所有 IPC 处理程序（`desktop:projects`、`desktop:add-project`、`desktop:connect`、`desktop:rpc`）均在宿主端强校验 `event.sender` 与 `event.senderFrame`，确保仅受信任的主窗口可触发操作。
- **RPC 方法白名单**：仅允许合同明确约定的白名单方法（如 `session.*`、`turn.*`、`system.*`、`confirm.respond` 等）；未授权调用在宿主层立即拦截并报错。
- **令牌隔离（No Token in Renderer/Log）**：Python 后端生成的共享鉴权密钥（Token）仅供宿主与后端的 TCP 套接字建立首行鉴权，严格禁止传递至渲染进程或输出至日志。
- **帧大小限制与防御**：单帧上限 1 MiB（1,048,576 字节）；读取缓冲区超出限制或帧数据畸形时主动防御并关闭连接，防止缓冲区溢出。
- **请求超时与清理**：所有 RPC 请求具备超时兜底；后端进程异常退出或断开时，立即拒绝（Reject）所有处于等待状态的 Promise。
- **优雅退出防僵尸**：宿主关闭或切换项目时，先关闭 Python 进程的 `stdin` 管道（触发 EOF），配合有界的 SIGTERM（2秒）和 SIGKILL（1.5秒）降级清理，杜绝后台僵尸进程。

---

## 本地环境与前置条件 (Prerequisites)

RinCode 桌面应用依赖本地现有的 Python 虚拟环境与代码仓：

1. **Python 运行时**：
   - 宿主优先查找环境变量 `RINCODE_PYTHON`（若配置）。
   - 其次查找源码根目录下的虚拟环境：`<sourceRoot>/.venv/bin/python`。
   - 最后回退到系统的 `python3`。
2. **源码根目录与工作树 (sourceRoot & PYTHONPATH)**：
   - 宿主启动 Python 后端时会将 `sourceRoot` 追加至 `PYTHONPATH`，确保 `rincode` 及其依赖能够被正确导入。
   - 开发模式下宿主自动向上递归定位包含 `pyproject.toml` 和 `rincode/` 的仓库根目录。
   - 打包本地应用（`.app`）时，构建脚本会将当前工作树的 `build-metadata.json` 打入应用包内，使本地打包出的 `.app` 能直接驱动本地开发环境与 `.venv`。

---

## 桥接 API 规范 (`ui-desktop/src/bridge.d.ts`)

渲染层通过全局 `window.rincode` 调用宿主能力：

```typescript
export interface Project {
  id: string;      // 稳定 ID（基于目录绝对路径的 16 位哈希）
  name: string;    // 项目展示名（默认目录名）
  path: string;    // 本地绝对路径
}

export interface BackendStatus {
  state: 'starting' | 'ready' | 'stopped' | 'error';
  message?: string;
}

export interface DesktopEvent {
  projectId: string; // 标识所属项目
  method: string;    // 'backend.status' 或后端推送的 'event' / 'confirm.request' 等
  params: any;
}

export interface DesktopBridge {
  projects(): Promise<Project[]>;
  addProject(): Promise<Project | null>;
  connect(projectId: string): Promise<BackendStatus>;
  rpc(projectId: string, method: string, params?: Record<string, unknown>): Promise<any>;
  onEvent(callback: (event: DesktopEvent) => void): () => void;
}
```

---

## 构建与运行命令

在 `ui-desktop` 目录下执行：

```bash
# 1. 安装依赖
npm install

# 2. 构建宿主模块与渲染包
npm run build

# 3. 运行宿主端单元与集成测试 (node:test)
npm test

# 4. 启动 Electron 桌面应用
npm start

# 5. 打包本地 macOS .app 应用程序
npm run package:mac
```

---

## 文件所有权清单 (Host Module Ownership)

本模块严格遵守跨团队所有权约束，仅管理以下范围文件：
- `ui-desktop/electron/**`：宿主入口、IPC 桥接、RPC 客户端、进程管理、存储、安全白名单及测试
- `ui-desktop/scripts/**`：构建（`build.mjs`）与本地打包（`package-mac.mjs`）脚本
- `ui-desktop/package.json`：宿主与桌面打包配置
- `ui-desktop/tsconfig.json`：TypeScript 编译配置
- `ui-desktop/index.html`：入口 HTML 模板（CSP 与离线调色板）
- `ui-desktop/README.md`：宿主设计与技术文档
