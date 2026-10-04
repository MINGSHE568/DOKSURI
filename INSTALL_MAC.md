# DOKSURI Mac 版安装说明

DOKSURI 是一款完全离线的桌面翻译软件：文本/文档/截图翻译全部在本机完成，文本与文档不经过网络。
本说明适用于 macOS 应用包 **DOKSURI3A.app**（内置中英双向翻译模型与中文 OCR 模型，开箱即用）。

---

## 一、获取安装包（二选一）

### 方式 A：已有 Mac —— 一键打包

1. 把 `DOKSURI` 项目文件夹完整拷贝到 Mac（须包含 `_models/`、`doksuri.ico`、`requirements.txt`）。
2. 打开「终端」，进入项目目录，执行：
   ```bash
   bash build.sh
   ```
   脚本自动完成：创建虚拟环境 → 安装依赖 → 由图标生成 .icns → PyInstaller 打包 → 校验。
3. 完成后得到 `dist/DOKSURI3A.app`；再执行 `bash make_dmg.sh` 可生成 DMG 安装包。

> 架构说明：在 M 系列 Mac 上构建即得到 arm64 原生版；在 Intel Mac 上构建即得到 x86_64 原生版。
> 如需一份安装包同时支持两种芯片，需安装 universal2 版 Python 及全部依赖后，
> 在 spec 中将 `target_arch` 设为 `"universal2"`（体积约翻倍）。

### 方式 B：没有 Mac —— GitHub 云构建（免费）

1. 把项目推送到 GitHub 仓库（注意 `_models/` 需一并提交；单文件超过 100MB 需用 Git LFS）。
2. 打开仓库 **Actions** 页 → 选择 **build-mac** → **Run workflow**。
3. 构建完成（约 20-40 分钟）后，在该次运行页面下载 **DOKSURI3A-macos** 产物，
   内含 `DOKSURI3A-3.0.0-arm64.dmg` 安装包。
4. 仓库已内置 workflow（`.github/workflows/build-mac.yml`）；macos-14 为 M 系列原生包，
   需要 Intel 包时把 `runs-on` 改为 `macos-13` 再跑一次。

> 说明：PyInstaller 官方明确不支持跨平台交叉打包（Windows 上无法直接生成可运行的 .app），
> 云端 macOS 构建是无需 Mac 时最可靠的方式。公开仓库 macOS 构建免费；私有仓库消耗额度较高。

---

## 二、安装步骤（DMG 拖拽安装）

1. 双击 `DOKSURI3A-3.0.0-arm64.dmg` 挂载磁盘映像。
2. 在弹出的窗口中，把 **DOKSURI3A.app** 拖入 **Applications** 文件夹。
3. 弹出磁盘映像（访达侧栏 ⏏），从「启动台/应用程序」启动 DOKSURI。
4. 首次启动会自动把内置的中英翻译模型与中文 OCR 模型复制到
   `~/Library/Application Support/DOKSURI/models`，**无需联网下载任何基础模型**。

### 首次打开被 Gatekeeper 拦截？

未经过 Apple 公证签名的应用首次打开会提示"无法验证开发者"：

- **推荐**：在应用图标上**右键 → 打开 → 打开**（仅首次需要）；
- 或终端执行（解除下载隔离标记）：
  ```bash
  xattr -cr /Applications/DOKSURI3A.app
  ```
- 若提示"已损坏，无法打开"，同样执行上面的 `xattr -cr` 命令即可（ad-hoc 签名的常见提示，非病毒）。

---

## 三、权限开启方法

### 屏幕录制（截图翻译必需）

macOS 出于隐私保护，应用读取屏幕内容前必须获得「屏幕录制」授权：

1. 启动 DOKSURI，点击「截图翻译」或按 `Cmd+Shift+O`，软件会弹出授权指引。
2. 点击「打开系统设置」，进入 **系统设置 → 隐私与安全性 → 屏幕录制**。
3. 在列表中找到 **DOKSURI** 并开启开关（若列表没有 DOKSURI，点 ➕ 从应用程序目录添加）。
4. **退出并重新启动 DOKSURI**（授权对已运行进程不生效，必须重启）。
5. 再次使用截图翻译即可正常框选识别。

> 屏幕画面仅在本机做 OCR 识别与翻译，全程不会上传或联网。

### 无需授权的功能

文本翻译、文档翻译、翻译历史、模型管理不依赖任何系统权限。

---

## 四、常用快捷键（macOS 标准按键）

| 功能 | 快捷键 |
|---|---|
| 快速翻译（原文为空时自动取剪贴板） | `Cmd+T` |
| 截图翻译 | `Cmd+Shift+O` |
| 设置 | `Cmd+,`（屏幕顶部菜单栏 DOKSURI → 设置…） |
| 退出 | `Cmd+Q` |

框选截图时：拖动选区，**Esc 或右键（触控板双指轻点）取消**。

---

## 五、常见问题

**1. 双击没反应 / 提示"已损坏"？**
执行 `xattr -cr /Applications/DOKSURI3A.app` 后再打开（见上文 Gatekeeper 部分）。

**2. 截图翻译只能截到桌面壁纸，窗口内容是空的？**
屏幕录制权限未授予或未重启应用，按第三节步骤开启并**重启 DOKSURI**。

**3. 屏幕顶部菜单栏应用名显示异常？**
从 DOKSURI3A.app 启动则显示 DOKSURI；若用 `python main.py` 直跑，显示 Python 属正常现象。

**4. 我的 Mac 是 Intel 芯片，能用 arm64 包吗？**
不能。请用 Intel Mac（或 GitHub Actions 的 macos-13）构建 x86_64 版本。

**5. 模型存在哪里？如何删除？**
- 翻译历史：`~/Library/Application Support/DOKSURI/history/history.db`
- 模型缓存：`~/Library/Application Support/DOKSURI/models`
- 菜单栏「工具 → 模型管理…」可查看/下载/删除各语种模型，点每行「文件夹」按钮可在访达中打开所在目录。

**6. 支持哪些语言？**
简体中文、英语、日语、韩语、法语、德语、俄语、西班牙语互译（非英语语对经英语中转）。
内置中英模型；其他语种在「模型管理」中点击下载一次后即可完全离线使用。

**7. 完全离线可用吗？**
是。翻译、OCR、文档处理全部本地运算；联网仅发生在你主动下载新语种模型时，且只下载模型文件，不发送任何文本。

**8. 界面在高分屏下模糊？**
应用已声明 Retina 支持（NSHighResolutionCapable），若仍模糊请确认系统缩放设置为默认"Retina 显示"。

---

DOKSURI v3.0.0 · 完全离线 · 文本不离开本机
