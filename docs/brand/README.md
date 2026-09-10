# RinCode 标识

原创的几何 **R**：上半部以终端箭头作为负形，斜切笔画呼应代码的执行方向。界面使用单色字形，macOS 图标使用白色字形与黑色圆角底板；不依赖渐变和细线，在小尺寸下仍可识别。

- [标识 SVG](mark.svg)：透明底、黑色字形。
- [首页横幅](banner.svg)：黑白与中性灰的品牌展示。
- [应用图标源文件](../../ui-desktop/assets/icon.svg)、[PNG](../../ui-desktop/assets/icon.png)、[macOS ICNS](../../ui-desktop/assets/icon.icns)。
- 界面的 `LogoIcon` 位于 [icons.tsx](../../ui-desktop/src/icons.tsx)，沿用同一条矢量路径。修改字形时同步这几个源文件。

图形由本项目以 SVG 绘制，按仓库的 Apache-2.0 许可证提供。README 的组织参考 [Zed](https://github.com/zed-industries/zed)、[uv](https://github.com/astral-sh/uv) 和 [Void](https://github.com/voideditor/void) 的项目介绍、安装入口与贡献说明；未使用它们的图形资产。

## 重新生成应用图标

应用构建直接使用已提交的图标，无需安装额外浏览器。只有修改 `icon.svg` 后，才需在 macOS 上执行：

```bash
cd ui-desktop
npm ci
npx playwright install chromium
node scripts/generate-icons.mjs
npm run package:mac
```

也可以用 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` 指定已安装的 Chromium 可执行文件。生成脚本用浏览器渲染 SVG，再通过 macOS 的 `sips` 和 `iconutil` 生成标准尺寸的 ICNS。

## 产品截图

`docs/desktop/` 下的产品 PNG 来自运行中的 RinCode 桌面端，使用隔离的演示项目和示例会话。它们展示当前实现的界面与交互，不表示一次真实模型任务的执行记录，也不包含用户的私人会话或配置。
