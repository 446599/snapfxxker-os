# snapfxxker-os · U1 四组件工程

目标是让 U1 + ACE 稳定工作：对照原厂进退料流程修复卡态、减少误报，普通失败后能取消、恢复并继续操作。

当前工作从 `main@629555c` 整理，**multiACE 活跃源码已收敛到 `packages/multiace/runtime/`**，本轮版本见 [runtime/VERSION](packages/multiace/runtime/VERSION)。此前 Flow.13/15 的代码和修复来源已记录；旧 provider 与历史交付快照保留供追溯。

## 四个组件

| 组件 | 入口 | 职责与产物 |
| --- | --- | --- |
| **1. 原厂固件** | [firmware/stock](firmware/stock/README.md) | 原厂 `2.1.0.157` 镜像来源与校验；保持原始 `.bin` 不变 |
| **2. 屏幕显示包** | [packages/screen](packages/screen/README.md) | 界面、字体、显示服务与用户操作适配；独立屏幕包 |
| **3. multiACE 包** | [packages/multiace](packages/multiace/README.md) | 主机侧进退料、缓冲控制、RFID、余量、Web 与恢复；完整应用或代码更新包 |
| **4. ACE 自定义固件** | [firmware/ace](firmware/ace/README.md) | ACE 2 Pro MCU 镜像与补丁；沿用 `1.1.60O` 配方，独立生成 `.bin` |

统一索引为 [components.json](components.json)，统一命令入口为 `python build.py`。各组件独立版本；应用源码更新不等于整机镜像或 MCU 已经升级。

## 本轮修复

- 完整退料成功后，结束 `AUTO_UNLOAD` / `PRINT_AUTO_UNLOADING`，允许下一次操作或继续打印。
- 切换自动送料开关时保留真实的“已装料完成”状态；已装料的重复 LOAD 无动作返回。
- 料源预检失败转换为普通 Klipper 命令错误，避免被分发器误判为内部异常并 shutdown。
- 热端压料时 STOP 未确认直接结束当前操作，避免外层异常捕获后继续挤出。
- 恢复 Anycubic / OpenSpool NTAG 读取，修正长 NDEF 数据，并把正常未识别结果保留为可重试信息。
- 修复 multiACE 改为普通目录后原生 UI 仍按子模块校验而构建失败，以及安装时 root 写入 `0700 __pycache__` 的权限回退。

根因、原厂固定链接、实际验证与待上机范围见 [2026-11-20 修复记录](docs/maintenance/20261120-modular-recovery.md)。

## 常用命令

```sh
# 查看四层入口
python build.py --plan

# 原厂镜像：只校验，不改写、不刷机
python build.py stock --plan

# 屏幕源码包：不含可执行 ARM 程序
python build.py screen --source-only --output-dir dist/screen-source

# 完整 multiACE 应用：使用固定的第三方依赖
python build.py multiace

# 仅代码更新：要求设备已有对应 Python 依赖
python build.py multiace --code-only

# ACE MCU 配方：需要用户提供已固定的原厂 MCU 镜像
python build.py ace --plan

# 主机行为回归
python -m unittest discover -s packages/multiace/tests -p 'test_*.py'
```

构建入口不触发设备连接、加热、电机动作或刷机。当前屏幕和 multiACE 安装适配沿用 233/PAXX 的服务与应用目录；分成四层不意味着已经实现原厂裸系统的一键安装桥接。具体安装前提由各组件 README 说明。

## 避免再次混乱

1. **修改当前 multiACE 只改 `packages/multiace/runtime/`。** `providers/multiACE` 是上游参考和 11 月 7 日的旧候选，不是本轮运行包输入。
2. **历史产物不当当前源码。** `deliverables/` 中的 Flow.13/15 和 `bundles/233-dev.2` 留作追溯；当前构建只从明确固定的旧快照取第三方依赖。
3. **历史整包单独保留。** `build-233.sh` / `233-firmware.json` 仍描述 UI.8 + single.5 旧整包，不会自动包含 Flow.16。它们没有改名冒充新的全功能固件。
4. **一个组件只维护一个版本源。** 不把屏幕版本、multiACE 版本与 ACE MCU 版本拼成一个模糊“最新版本”。

## 给 AI 的交接

自动读取的工程约定在 [AGENTS.md](AGENTS.md)，可直接交给下一位 AI 的完整说明在 [docs/AI_HANDOFF.md](docs/AI_HANDOFF.md)。核心要求：**基于实际故障做最小修复，复用原厂行为，禁止叠加猜测性 guard、锁存和重试；普通失败要清理并退出，真实 STOP/温度/料路边界仍保留。**

## 来源与许可

原厂源码行为参考 [Snapmaker/u1-klipper](https://github.com/Snapmaker/u1-klipper/tree/7ac453b50fe6779b2ec0b759038a17da5e6d671d)。工程保留 PAXX、multiACE、HelixScreen、Simon-CR 和相关贡献者的来源与许可。参阅 [LICENSE](LICENSE)、[PAXX 原始说明](README.upstream.md)、各组件许可和 [旧集成说明归档](docs/archive/README-233-ui8.md)。
