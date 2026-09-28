# 官方游戏启动器的一次性授权

当前电脑的官方启动器为 `E:\Naraka\tool\LootHoarder.exe`，工作目录为 `E:\Naraka`。已确认网易数字签名有效，程序清单要求 `requireAdministrator`；桌面快捷方式没有额外设置管理员运行。

`scripts/Game-LauncherTask.ps1` 创建按需运行的 Windows 任务 `NarakaCheckinAssistant-GameLauncher`。任务只直接运行上述程序，不带参数，不运行 Python、PowerShell 或可替换的辅助脚本。使用当前 Windows 账号、交互式登录及最高权限；不保存密码、不设置每天触发、不更改 UAC。用户必须已登录且桌面可交互。

在项目目录执行：

```powershell
# 只检查，不修改任务，也不启动游戏
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Game-LauncherTask.ps1 -Mode Inspect

# 首次安装：由用户在 Windows UAC 弹窗中允许一次
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Game-LauncherTask.ps1 -Mode Install

# 后续按需启动；请求成功不代表游戏登录成功
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Game-LauncherTask.ps1 -Mode Start

# 撤销本项授权：需要管理员确认，不删除游戏或签到记录
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Game-LauncherTask.ps1 -Mode Remove
```

安装后读回任务验证执行路径、工作目录、无参数、无触发器、用户和权限级别。任务权限限制为 SYSTEM/管理员可修改，当前用户可读取和运行。已有同名但内容不同的任务不会被覆盖。后续启动会检查官方程序签名；路径或发行者变化时应重新核对。

最近一次操作记录保存在 `%LOCALAPPDATA%\NarakaCheckinAssistant\game-launcher-setup.json`。`installed` 仅表示计划任务已配置；`launch_requested` 仅表示 Windows 接受启动请求，均不表示已登录或已领取积分。

此步骤尚未接入签到定时器。完整自动登录需要另外验证启动器按钮、游戏大厅、登录状态保持、提权窗口可操作性和小程序领取结果。扫码、验证码、更新器追加的权限请求仍可能中断无人值守流程。

## 本机验证记录（2026-09-24）

- 用户完成一次 UAC 授权，任务已注册。读回确认用户、最高权限、交互登录、唯一动作、无参数、无触发器和访问权限。
- 首次读回校验误将 Windows 返回的用户名与 SID 直接比较，并把空触发器集合算作一个元素；已修正为解析 SID 和过滤空值，重新检查通过，没有要求再次授权。
- 普通权限执行 `-Mode Start` 返回成功，并观察到 `LootHoarder.exe` 的“永劫无间启动器”窗口，页面显示“启动游戏”。没有发起第二次管理员授权。
- 桌面控制工具截图接口报 `SetIsBorderRequired failed: 不支持此接口 (0x80004002)`，控件树仅返回窗口及禁用窗格。本机只读截图确认启动器正常显示。
- 后续普通权限输入测试未成功，原生光标定位 `SetCursorPos` 报错；没有确认点击“启动游戏”，没有确认游戏登录，也未执行新的积分领取。尚不能单凭此报错判断所有控制方式均不可行。
- 截图保存在 `test-output/game-launcher/launcher.png` 和 `before-start.png`。只证明启动器已打开。
- 补测 `LauncherGame.exe` 并指定安装目录为工作目录后，观察到 Windows `consent.exe`，备用入口仍触发 UAC；它不作为免确认启动方案。这个额外提示来自备用入口测试，不是已注册任务的调用。
- 新增 5 项无副作用的任务校验测试：SID/用户名两种返回形式、空触发器、替换动作/参数、其他用户/非交互身份、新增触发器/错误工作目录。均已通过，测试不注册任务、不启动游戏。
- 全量测试最初受到真实 UAC 桌面状态影响，3 项模拟动作测试返回 `desktop_busy`。修复测试对宿主桌面的依赖，并单独验证桌面不可用时禁止点击；生产桌面保护未修改。最终 66 项测试全部通过（12.232 秒），结果见 `test-output/game-launcher/tests.txt`。
- 用户手动点击启动游戏后，已成功用本机截图读取游戏本体：窗口 `Naraka`，2560×1600，停在公告及“Space 继续”页面。通过桌面工具发送一次空格后，截图仍为公告页，未验证登录完成。
- 只读令牌检查确认游戏进程为高完整性级别且已提权，本机测试进程为中完整性级别且未提权。Windows 对普通 SendInput 存在跨完整性级别限制，这是后续本地自动输入必须解决的问题；尚不能断言本次工具按键无效只有这一原因。
- 游戏截图和探测结果：`test-output/game-launcher/game-opened.png`、`game-after-space.png`、`game-probe.json`。现有授权仅启动官方程序，没有给自动操作模块追加管理员权限。
- 用户继续公告后，页面出现网易游戏的已保存账号选择框及“进入游戏”按钮；当时没有密码输入框或验证码。仅能证明保存了账号入口，不能据此断言登录凭据仍然有效。截图 `game-next-page.png` 含账号信息，仅保存在本机，不作公开交付预览。
- 用户点击账号框的“进入游戏”后，账号框消失，停在游戏标题页的“点击屏幕进入”。该页仍不是大厅，尚不能判定游戏登录任务条件已经满足。截图为 `game-after-entry.png`。
- 用户继续标题页后，已见到大厅背景及“白泽月卡”奖励弹窗，背景含“开始游戏”。据此确认本次通过已保存账号进入了游戏；操作由用户完成，没有自动领取月卡或小程序积分。截图 `game-after-title.png` 含角色标识，只在本地保存。
- 本次观察到的路径：官方启动器“启动游戏” → 公告“Space 继续” → 网易已保存账号“进入游戏” → 标题页“点击屏幕进入” → 大厅及登录奖励弹窗。已保存账号在本次有效；这不保证未来不会要求重新登录或验证。
