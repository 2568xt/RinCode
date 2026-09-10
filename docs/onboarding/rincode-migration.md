# RinCode 命名与接口迁移

项目展示名称为 **RinCode**，Python 包和命令为 `rincode`，发行包为
`rincode-harness`。评测系统展示名称为 **RinBench**，Python 模块为
`benchmarks.rincodebench`，源码位于 `benchmarks/rincodebench`。

## 安装与验证

在源码根目录执行 `./install.sh`，Windows 使用 `./install.ps1`。
安装器构建所需的 TUI 产物并注册 `rincode` 命令。安装完成后可运行：

```bash
rincode --version
rincode --help
rincode onboard --skip-memory
```

源码模块入口为 `python -m rincode`。独立使用安装器时，需要通过
`RINCODE_WHEEL_URL` 指定可信的 wheel 地址或本地路径；安装器不推测发布地址。

## 接口和状态目录

- Python 配置：`rincode.config.rincode.RinCodeConfig`、`load_rincode_config`。
- 环境变量：`RINCODE_*`、`RINCODEBENCH_*`。
- 状态目录：`~/.rincode`、项目内 `.rincode`；`RINCODE_HOME` 可指定用户状态目录。
- 插件清单：`rincode-plugin.toml`，版本约束字段 `plugin.rincode`。
- 插件入口组：`rincode.plugins`，技能 metadata 字段 `rincode`。
- RPC 版本字段：`rincode_version`；导出、评测和遥测协议也使用新名称。

本次迁移不保留旧名称的导入、环境变量、目录回退、清单别名或协议兼容。
已有用户应先备份自己的配置与状态，再显式迁移目录、环境变量和插件配置；
客户端与服务端需要一起更新。安装后应检查命令解析位置，确认运行的是新安装的
`rincode`。

## 第三方适配边界

Myna 适配器必须提供 `myna.integrations.rincode` 模块及
`myna.rincode-app-integration.v1` 协议。插件身份、发行版本和兼容范围继续
执行严格校验。这是 RinCode 要求的适配契约，不代表外部 Myna 已发布对应实现。
目前该契约通过模拟适配器测试，尚未完成真实适配器集成验证。暂不使用外部
Memory 时，可通过 `--skip-memory` 完成向导；Local Skills、Session 和 Context
仍可使用。

第三方依赖保留发布者指定的包名，作者署名与许可条款保持有效。依赖的原名不属于
产品命名。历史评测资料若经过名称改写，其原始字节哈希不再适用于改写副本；
评测结论应以明确标识的产物版本和执行证据为准。
