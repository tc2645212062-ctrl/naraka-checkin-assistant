# 永劫无间微信小程序签到助手

Windows 本地中文桌面工具，使用正常微信界面识别并执行任务。Python + PySide6 + 本地 OCR，运行不需要 GPT、Codex 或 OpenAI API。

## 能做什么

- 日历签到、登录游戏积分、名片、海报、阅读、金钥匙六项任务，逐项检查并验证结果。
- 每日定时、手动执行、有限重试、失败通知与 SQLite 历史记录。
- 先确认端游/手游选中状态，再进入福利中心，防止把手游结果计入端游。
- 已完成任务跳过；未识别、网络错误、活动不存在、需要人工验证分别记录。
- 可选沿用保存账号登录端游；确认大厅后退出本轮启动的游戏，再领取积分。
- 微信临时任务窗口自动清理。

## 当前验证范围

2026-09-28 在开发电脑验证过端游六项任务完成、游戏冷启动进入大厅与正常退出。部分流程经过故障修复后补跑，不能视为所有电脑或未来活动的无人值守保证。详细过程见 [验证记录](docs/VALIDATION.md)。仓库不包含个人实机截图和数据库。

**这是源码项目，尚未提供开箱即用的通用 EXE 安装包。** 微信版本、缩放、活动页面和游戏路径变化可能需要调整。

## 安装运行

需要 Windows 10/11 x64、Python 3.12、桌面微信，Windows 桌面已登录并解锁。

```powershell
git clone https://github.com/tc2645212062-ctrl/naraka-checkin-assistant.git
cd naraka-checkin-assistant
powershell -NoProfile -ExecutionPolicy Bypass -File .\Setup.ps1
.\Start.cmd
```

安装脚本在仓库目录创建 `.venv`。启动脚本优先使用该环境，同时兼容开发电脑已有的 `D:\NarakaCheckinAssistant\.venv`。首次安装需要联网，OCR 模型随依赖安装，识别在本机进行。

首次使用需自行登录微信、打开官方小程序并绑定游戏角色。在“执行计划”选择微信路径、平台、角色关键词和执行时间；“每日任务”可修改任务识别文字。新配置默认暂停，时间 22:00，可改为 05:00 后启用。

启用定时会注册当前 Windows 用户的每日启动任务；退出助手后，下次设定时间仍可由 Windows 启动。暂停会停用该任务。电脑关机、Windows 锁屏、微信登录失效时不能保证执行。任务会占用前台桌面。

### 可选的端游自动登录

自动登录默认关闭。受限操作模块和启动器任务需要用户在场完成一次 UAC 授权，不关闭或绕过 UAC。参见 [启动器安装](docs/GAME-LAUNCHER.md) 和 [操作模块](docs/GAME-INPUT.md)。

当前模块严格绑定以下安装路径，并检查网易签名：

- `E:\Naraka\tool\LootHoarder.exe`
- `E:\Naraka\program\NarakaBladepoint.exe`

其他安装路径目前需要同步修改相关源码、任务脚本和检查后重新构建、授权安装。请勿直接复制开发电脑的高权限任务。模板只验证过有限的分辨率和缩放范围。扫码、验证码、账号限制、内存不足或未识别页面会停止，不保证自动处理。运行中的对局不会被自动结束。

## 数据与分享

运行数据在 `%LOCALAPPDATA%\NarakaCheckinAssistant`，包括配置、日志、数据库与截图。它们可能含角色和账号信息，请勿上传到公开仓库或 Issue。提交问题时提供脱敏后的错误信息即可。

仓库保留识别所需的图标裁切与脱敏测试图片，不包含聊天、登录令牌、个人运行记录、虚拟环境或游戏文件。游戏与微信图像标识属于对应权利人，本项目非官方产品。

名片任务取消联系人选择，不发送聊天；海报任务仅生成预览。若活动以后要求实际发送才计分，程序将停止并保留未完成状态。金钥匙按官方文章的发布日期与可见有效期判断，过期或不确定时不提交。

## 测试与模拟

```powershell
.\Test.cmd
.\.venv\Scripts\python.exe app.py --smoke
.\.venv\Scripts\python.exe app.py --preview --scenario partial
.\.venv\Scripts\python.exe app.py --demo success
.\.venv\Scripts\python.exe app.py --demo already_signed
.\.venv\Scripts\python.exe app.py --demo manual_required
.\.venv\Scripts\python.exe app.py --demo network_error
```

模拟使用临时数据，不操作微信，也不会写入真实完成记录。要运行原生模块的模板测试，先执行 `scripts/Build-GameInput.ps1`；构建不需要安装模块或管理员授权。GitHub Actions 只构建和运行测试，不执行真实签到。

## 项目结构

- `app.py`：启动入口、模拟和一次执行模式。
- `assistant/`：界面、配置、定时、存储、任务引擎。
- `assistant/desktop/`：微信窗口操作、OCR、平台校验与任务适配层。
- `native/`：受限游戏操作模块源码与控件模板。
- `scripts/`：安装、构建与诊断脚本。
- `tests/`：模拟行为、回归及图片识别测试。
- `docs/`：可行性、限制与验证记录。
