# 2026-11-20：减少误报、恢复原厂收尾行为、拆分四组件

## 基线与结论

本轮从 `446599/snapfxxker-os/main@629555c33ecd569f68bfd8e87d31d28b437df892` 开始。这个提交把 `providers/multiACE` 从子模块改为普通文件，同时上传了早期工作区修改；文件的推送时间不等于其运行逻辑比现场快照新。

本轮当前运行源码为 `packages/multiace/runtime/`：完整 Flow.13 主机源码，加上已推送 `a887123946320767db44e8848602651f3f60b37a` 的 Flow.15 修复，再加入本轮修复。版本为 `1.27b-233.20261120-flow.16`。不将 11 月 7 日的 provider 候选覆盖到更新的运行源码。

## 已修复的行为

| 现象 | 根因 | 本轮修改 |
| --- | --- | --- |
| 完整退料成功后仍停在“自动退料” | `FEED_AUTO` 完整退料不带 STAGE，即 `None`，收尾条件却只包含 doing/cancel | 将 `None` 纳入与原厂一致的收尾条件；空闲回 IDLE，打印中结束退料动作并保留可继续打印状态 |
| 自动送料开关变化后丢失“已装料” | 模式更新覆盖 `LOAD_FINISH` | 保留成功装载状态；无模块且未成功装载才恢复 NONE，按原厂行为处理 |
| 已有料时再按 LOAD 也因旧元数据报错 | 无动作操作在返回前仍执行新来源预检 | 成功装载、无错误且喷头传感器确认有料时直接返回，不启电机、不加热 |
| 普通来源预检失败变成整机内部错误 | `FeedSupervisionError` 在命令 try 外泄漏为 RuntimeError；官方分发器会 invoke_shutdown | 转换成标准 `gcmd.error`；空闲普通失败，打印中使用可恢复暂停 |
| 热端压料 STOP 失败后还继续挤出 | 内层重新抛出的停止异常又被外层一般异常捕获并继续 | 单独向上传播 `StopUnconfirmedError`，进入已有清理路径，不再开启助送料或进入挤出 |
| 正常 Anycubic/OpenSpool NTAG 无法读取 | expected_uid 分支先拒绝普通身份，再拒绝 NTAG sentinel | 稳定 UID→一次原始页读取→UID 核对；复用既有回停后身份提交流程 |
| 长 NDEF 或带 ID 的标签解析错误 | 长记录长度偏移错误，编码只支持 1 字节长度，未处理 ID 字段 | 正确处理记录长度/ID/长 payload；异常格式作为未识别结束 |
| 失败的页读取被当作有效数据 | 发送失败仍可能读旧 RX，缺失页被零填充 | 读取失败直接返回未识别，不拼接错误偏移、不保存伪数据 |
| 无标签、暂未读到标签显示红色失败 | 后端 pending 被前端 normalizeState 丢掉，模板只判断 ok | 保留 pending，显示中性信息；只针对当前设备和槽位显示重试识别按钮，沿用既有忙碌/STOP禁用条件 |
| Anycubic 识别成功却被分类成失败 | 成功日志大小写不符合实际命令结果分类器 | 对齐已有成功标记；余量未知不会改成失败 |
| 11 月 20 日 vendoring 后 UI 构建校验必然失败 | validator 仍按旧 multiACE gitlink SHA 检查普通目录 | 将 provider 当普通受跟踪源码目录，screen-apps 子模块固定值保持原检查 |
| 安装后出现 root 私有字节码目录 | root 在 umask077 下进行 Python import，重新创建0700 __pycache__ | 两份安装回调 import 使用 `python -B`，不生成新的字节码目录 |

这些修改没有新增通用保护框架、永久锁存、全量重复读卡或无上限重试。Flow.15 已有的缓冲控制和有限恢复继续保留；真实 STOP 未确认不会被降级成成功。

## 原厂行为依据

本轮直接读取了官方 `Snapmaker/u1-klipper@7ac453b50fe6779b2ec0b759038a17da5e6d671d`：

- [filament_feed.py](https://github.com/Snapmaker/u1-klipper/blob/7ac453b50fe6779b2ec0b759038a17da5e6d671d/klippy/extras/filament_feed.py)：AUTO_MODE 对已成功装料状态的保留、完整退料 None/doing/cancel 的状态收尾。
- [gcode.py](https://github.com/Snapmaker/u1-klipper/blob/7ac453b50fe6779b2ec0b759038a17da5e6d671d/klippy/gcode.py)：标准命令错误与内部异常的区别，非标准异常会触发内部错误关停。

这属于官方源码行为比对，尚未通过本次实机测试确认所有现场时序。

## 工程拆分

| 层 | 当前入口 | 本轮实际输出或状态 |
| --- | --- | --- |
| 原厂固件 | `firmware/stock/` | 固定镜像来源、SHA-256、原样校验/复制入口；未下载、改写或刷入原厂镜像 |
| 屏幕显示包 | `packages/screen/` | 独立源码与构建输入包；保留真正 ARM 编译入口，本轮未执行 ARM 编译 |
| multiACE 包 | `packages/multiace/` | 当前运行源码、完整应用构建入口、可重复代码更新包；第三方依赖从固定快照按文件注入 |
| ACE 自定义固件 | `firmware/ace/` | 已有1.1.60O补丁的单一源码和独立构建入口；MCU补丁内容未改，未生成/刷入新MCU镜像 |

所有入口由 `components.json` 和根 `build.py` 管理。AI 约定写入 `AGENTS.md` 与 `docs/AI_HANDOFF.md`。旧 README、旧整包配方与交付快照保留，历史记录不作为本轮已经完成的设备验证。

## 实际验证

- 当前 multiACE 行为与打包回归：**162 项通过**，包含原有 Flow.15、原厂入口状态、NTAG、真实结果回调、Web pending、有限恢复、缓冲与打包测试。
- vendored provider 布局回归：**3 项通过**。
- 安装权限回归：**2 项通过**，真实执行 Python import 并检查字节码和目录权限。
- 合计：**167 项测试通过**。Python 模块语法检查与 `app.js` 的 `node --check` 通过。
- `scripts/native-ui/validate.py --source-only --partial-tree` 通过；本地没有旧 single.5 的10MB归档，未把完整source-only校验标为通过。
- 屏幕源码包两次构建与解包后重建逐字节一致，96个源码输入逐一核对，缺少必要补丁时正确退出。
- multiACE 构建测试确认同源不同文件名/mtime的归档一致、只注入vendor依赖且不覆盖当前应用代码、共享ACE补丁链接打成普通文件。

### 本地构建产物

| 文件 | 类型 | 字节数 | SHA-256 |
| --- | --- | ---: | --- |
| `multiace-code-1.27b-233.20261120-flow.16.tar.gz` | 需已有Python依赖的代码更新包 | 1205060 | `effd007cfa25dc23280c6dd281a35fe7a4bf67722d74ab1d036d23514c2a9dd9` |
| `snapfxxker-screen-source-20261120.tar.gz` | 屏幕源码与构建输入，不含ARM程序 | 1932127 | `9504a40af165ef32ebce24154e2f55a8ec66cc62600159beaa8fddad170f6751` |

完整 multiACE 构建入口已实现，但本次本地工作区缺少固定vendor中的724个文件，不能生成含依赖的完整应用。构建器对此明确失败，不将代码包标为完整包。独立 GitHub 工作流会在完整仓库 checkout 上执行测试、构建完整主机应用与屏幕源码包；工作流结果另行记录，不能把“已写workflow”算成“CI已通过”。

## 尚未完成的设备验证

本轮没有连接、重启或刷写打印机/ACE，没有执行真实加热或进退料。下一次上机重点检查：完整退料后能再次操作；自动模式切换保留已装料状态；普通料源拒绝不造成shutdown；无标签可结束并重试；真实STOP故障不能继续挤出。连续热换料、机械阻力、现场传感器时序和余量精度仍依赖实机证据。
