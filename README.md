# snapfxxker-os（233 固件）

面向 **Snapmaker U1 + Anycubic ACE Pro / ACE 2 Pro** 的集成固件工程。以 **PAXX 12 Extended Firmware** 为系统底座，深度整合 **MultiACE（233 定制分支）** 与 **自制 480×320 原生触屏界面（HelixScreen 233）**，支持多台 ACE 混插（含原厂 / OpenACE / `ace2k` Klipper-MCU 固件）与最多 16 槽多色供料管理。

> **官网介绍页**：[https://446599.github.io/snapfxxker-os/](https://446599.github.io/snapfxxker-os/)（含实机 480×320 界面交互预览与原厂功能对比）  
> **最新预发布固件**：`2.1.2-233-paxx8d8af0d.2`（[前往 Releases 下载](https://github.com/446599/snapfxxker-os/releases)）
> **开发分支 ACE 固件刷写**：MultiACE flow.15.113 在网页里为每台 ACE 2 Pro 提供 ACE2-Open V1.2.04O 与 ace2k 0.16.0-233b 两个刷写选项。[组件、构建与使用说明](bundles/multiace-firmware-selector/README.md)。

---

## 核心特性

### 1. PAXX 12 扩展固件底座（`2.1.2-paxx12-develop-8d8af0d`）
- 基于官方 `2.1.2.159_20261110113009` 与 PAXX 12 `extended-multiace` 构建。
- 完整保留 PAXX 的 1080p/30fps WebRTC / MPP 摄像头链路（`stream-webrtc`、`capture-v4l2-raw-mpp`、`libv4l2-imposter.so`）与 Fluidd Web 界面。
- 融合 `extended-pkg` 扁平 ZIP 解压能力与 MultiACE 旧版目录回滚保留机制。

### 2. MultiACE 233 运行时（`1.27b-233.20261122-flow.15.112`）
- **多固件混插兼容**：支持 **ACE 1**、**ACE 2 Pro 原厂 / OpenACE（`V1.2.04O`）** 与 **`ace2k`（Klipper-MCU 开源固件）** 在同一台 U1 上混合挂载与协同运行。
- **`ace2k` 原生驱动（[ace_protocol_ace2k.py](providers/multiACE/multiace/klipper/extras/ace_protocol_ace2k.py)）**：
  - 支持 **Bambu Lab MIFARE 1K** 与 **Anycubic NTAG** RFID 标签读取与识别回填。
  - 支持插料自动进丝停泊（`tag_read_load`）与精准编码器行程追踪回抽，消除长管过度回退。
  - 完整适配烘干启停与状态同步，修复多协议请求体内部控制键序列化异常。
- **Multi 模式后台换料支持**：提供 `ace_bg_swap` Multi 模式后台换料与停靠交接保护（默认关闭，按需启用）。
- **V2 卸料死锁与稳定性修复**：
  - 修复原厂 V2 在 `phase3 NO FLOW` 失败（`load_failed=True` 且喷头到料开关触发）后，执行 `ACE_UNLOAD_HEAD` 或前台换料回停时被 `_native_reconnect_block_reason` / `unload_source_matches` 误拦造成的死锁。
  - 修复 `pkg_post_install` 目录权限（统一 `0755`），避免 `umask 077` 导致非 root 用户读取失败。
- **内置 ACE 2 Pro 固件**：随包附带 `V1.2.04O`；Releases 同时提供面向 **220V/50Hz 电网调优的 `ace2k-0.16.0-233b.bin`**（开启并行通道并适配 220V 烘干升温曲线）。

### 3. 自制 480×320 原生触屏界面（`helixscreen-233-1.2.0-233.20261124.4`）
- 基于 HelixScreen 深度定制的 U1 原生触屏 UI，统一黄白视觉布局与完整中文字体。
- 首页实时展示四个喷头温度、实际供料颜色与任务控制；ACE 页面整合 16 槽供料来源、耗材属性、RFID、自动续料与烘干入口。
- 配套 `u1-device-service` 设备服务，提供升级空闲检查、互斥保护、回环代理与槽位配置回退机制。

---

## 当前组件基线（`2.1.2-233-paxx8d8af0d.2`）

| 组件 | 来源 / 上游 | 当前版本 / 提交 |
| --- | --- | --- |
| **系统底层（PAXX 12）** | [SnapmakerU1-Extended-Firmware](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware) | `2.1.2-paxx12-develop-8d8af0d`（stock `2.1.2.159`） |
| **MultiACE 运行时** | [decay71/multiACE](https://github.com/decay71/multiACE) + 233 补丁集 | `1.27b-233.20261122-flow.15.112`（[ace2k-multi-bg-swap.patch](providers/multiACE/backports/ace2k-multi-bg-swap.patch)） |
| **自制原生屏幕** | [HelixScreen](https://github.com/prestonbrown/helixscreen) + 233 补丁集 | `helixscreen-233-1.2.0-233.20261124.4` |
| **ACE 2 Pro 固件（内置）** | OpenACE | `V1.2.04O` |
| **ACE 2 Pro 固件（可选）** | [tobecwb/ace2k](https://github.com/tobecwb/ace2k) + 220V / 并行通道补丁 | `ace2k-0.16.0-233b`（见 Releases） |
| **screen-apps** | [paxx12/screen-apps](https://github.com/paxx12/screen-apps) | `4a81b50d9b6ef7a2c253b45dd00fe6f732c9d203` |

---

## 下载、校验与升级注意事项

1. 从 [GitHub Releases](https://github.com/446599/snapfxxker-os/releases) 下载对应版本的 `U1_233_*_upgrade.bin` 与 `SHA256SUMS.txt`。
2. 刷写前务必校验 SHA-256：
   ```sh
   shasum -a 256 -c SHA256SUMS.txt
   ```
3. **升级前建议**：备份打印机 `/home/lava/printer_data/config`（尤其是 `extended/multiace/ace_vars.cfg` 与 `extended/helixscreen/settings.json`）；确认当前无打印任务且所有喷头处于待机冷却状态。
4. **验证边界说明**：
   - MultiACE `.15.112` 运行时、V2 `load_failed` 卸料死锁修复、`ace2k-0.16.0-233b`（RFID 读卡 / 插料自动进丝 / 45°C 220V 烘干 / 普通换料）均已在 4× ACE 实机完成验证；
   - `2.1.2-233-paxx8d8af0d.2` 整包固件已通过镜像分区完整性校验、嵌入包校验与 ARM64 chroot 全套冒烟测试；
   - Multi 模式后台换料（`ace_bg_swap`）默认关闭，建议先确认普通换料稳定后再按需开启测试。

---

## 仓库结构

```text
233-firmware/
├── 233-firmware.json                 项目元信息与历史验证记录
├── build-233.sh                      本地 233 固件构建入口
├── overlays/
│   ├── 233-dev.2/                    233 扩展包定义与配置覆盖
│   ├── 233-native-ui/                自制原生界面启动脚本、持久化与设备服务
│   ├── firmware-extended/            PAXX 基础扩展功能与 Web 界面
│   └── mods/multiace/                MultiACE 模块集成与包定义
├── providers/multiACE/
│   ├── backports/                    233 维护的功能与修复补丁集（含 ace2k / 后台换料 / 死锁修复）
│   └── multiace/                     MultiACE 源码树（含 ace_protocol_ace2k.py）
├── bundles/native-ui/                原生触屏源码清单、补丁链与许可文件
├── scripts/                          固件解包/打包、原生 UI 构建与校验脚本
├── deps/screen-apps/                 PAXX 屏幕应用子模块
├── docs/233/                         设计说明、改动记录与历史归档文档
└── README.upstream.md                上游 PAXX 原始说明
```

拉取子模块：

```sh
git submodule update --init --recursive
```

---

## 本地构建与开发

- 查看 `build-233.sh` 构建计划（不启动 Docker）：
  ```sh
  ./build-233.sh --plan
  ```
- 原生触屏 UI 的交叉编译、运行包固定与校验说明见 [原生 UI 构建说明](docs/233/native-ui.md) 及 [233 Native UI 工作流](.github/workflows/native_ui.yaml)。
- 历史 `233-dev.2` / `ui.4` 集成与设计记录保存在 `docs/233/` 目录。

---

## 开源致谢与许可

本项目遵循上游各组件原有版权与开源协议：
- **PAXX Extended Firmware**：[SnapmakerU1-Extended-Firmware](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware)（GPL-3.0，见 [LICENSE](LICENSE) 与 [README.upstream.md](README.upstream.md)）
- **MultiACE**：[decay71/multiACE](https://github.com/decay71/multiACE)（GPL-3.0，见 [providers/multiACE/LICENSE](providers/multiACE/LICENSE)）
- **HelixScreen**：[prestonbrown/helixscreen](https://github.com/prestonbrown/helixscreen)（GPL-3.0-or-later，见 [bundles/native-ui/licenses/README.md](bundles/native-ui/licenses/README.md)）
- **ace2k**：[tobecwb/ace2k](https://github.com/tobecwb/ace2k)（Klipper-MCU ACE 开源固件）
- **Filament-approximation**：余量估算模块改编自 WatchingWatches/Filament-approximation（Benedikt Jansson，MIT，许可原文见 [bundles/233-dev.2/licenses/Filament-approximation.txt](bundles/233-dev.2/licenses/Filament-approximation.txt)）
