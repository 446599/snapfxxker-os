# 233固件

面向 **Snapmaker U1 + Anycubic ACE** 的集成固件工程，以 PAXX Extended Firmware 为底座，集成 multiACE 的 ACE 控制、耗材管理与 Web 界面。

当前开发版本：`2.1.0-233-dev.3-ui.3`，处于**首批原生 UI 源码集成**阶段。黄白界面由 233 固件自己的源码补丁、启动 overlay 和构建入口管理；本修订尚未部署到打印机，也未发布 Release。新的 ARM64 运行包必须从当前补丁重新构建、通过校验并导入，才能继续制作固件包；仓库不携带上一版 UI 二进制冒充本次构建。

本次修复 `df0f5c3` 审查中的升级互斥、字体补丁链和回环代理问题，验证范围见 [修复记录](docs/233/native-ui-review-fixes-20261110.md)。旧 `ui.2` 产物对应原提交，不包含这些修复。

已经部署并启动验证的上一版是 `2.1.0-233-dev.2-perms.1`。其 Klipper、ACE 启动与数据核对记录保留在 [233-firmware.json](233-firmware.json) 的 `previous_validation`；这些记录不作为本次 UI 修订的实机验证。构建仍使用原来的 rsyncfix 基线和已验证的 multiACE 包。

## 源码基线

| 组件 | 上游 | 当前固定版本 |
| --- | --- | --- |
| PAXX 扩展固件 | [SnapmakerU1-Extended-Firmware](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware) | develop / `bb1fd3705ec9608d71122462b07556613dbbbb1f` |
| multiACE | [decay71/multiACE](https://github.com/decay71/multiACE) | v1.27b / `a1a7b91b1fd297c1486a13ea22c61e01499a196d` |
| screen-apps | [paxx12/screen-apps](https://github.com/paxx12/screen-apps) | PAXX 指定的 `4a81b50d9b6ef7a2c253b45dd00fe6f732c9d203` |
| 原生屏幕引擎 | [HelixScreen](https://github.com/prestonbrown/helixscreen) | `cf2e80ab6ccbf44b5ee389ffd0d68b4858ea9fd1` + [233 源码补丁清单](bundles/native-ui/manifest.json) |
| 系统基线 | 233 现场 rsyncfix 包 | `2.1.0-233-dev.1-rsyncfix.1`；原厂上游为 `2.1.0.157`，校验和见 [233-firmware.json](233-firmware.json) |

233 集成仓库为 [446599/snapfxxker-os](https://github.com/446599/snapfxxker-os)。本批变更在功能分支中审查；不向 HelixScreen 上游写入代码，也不更改 multiACE 的固定版本与现有映射配置。

multiACE 上游 main 在拉取时为 `1.50-pre`，其代码和历史已拉取并保留在 `providers/multiACE` 的 `origin/main`。初始集成锁定 PAXX 当前使用的 v1.27b；升级预发布版本需要单独评估接口、配置与 managed 包兼容性。

## 工程布局

```text
233-firmware/
├── 233-firmware.json        项目名称、开发版本、上游提交与包校验和
├── build-233.sh             233 命名的构建入口
├── overlays/               PAXX 基础功能及已有 multiACE 集成
├── overlays/233-native-ui/  233 原生界面启动、持久化设置和设备服务
├── bundles/native-ui/      固定源码、文本补丁、工具链及许可清单
├── scripts/native-ui/      还原、交叉编译、导入和运行包校验
├── providers/multiACE/     固定版本的 multiACE 源码子模块
├── deps/screen-apps/       PAXX 固定的屏幕应用子模块
├── docs/233/               233 的集成计划与初始化记录
└── README.upstream.md      保留的 PAXX 原始项目说明
```

后续恢复子模块：

```sh
git submodule update --init --recursive
```

## 构建入口与名称

查看构建计划，不启动 Docker：

```sh
./build-233.sh --plan
```

先使用 [233 Native UI 工作流](.github/workflows/native_ui.yaml) 为当前提交重建 ARM64 运行包，下载通过校验的 Actions artifact。将归档及相邻的 `.manifest.json` 放在同一目录，再导入：

```sh
python3 scripts/native-ui/pin_runtime.py --runtime /path/to/helixscreen-233-native-ui.tar.gz
python3 scripts/native-ui/validate.py
```

源码和构建规则的指纹必须与本地 checkout 相同。未导入、来源不匹配或仍是旧版运行包时，固件入口会退出并提示重新构建。完整步骤与本地构建命令见 [原生 UI 构建说明](docs/233/native-ui.md)。

完成导入后，实际固件构建使用：

```sh
./build-233.sh
```

该入口依次应用 `overlays/233-dev.2-perms.1` 和 `overlays/233-native-ui`，使用已校验的现场 rsyncfix 基线、本地离线 multiACE 快照及匹配当前源码的 ARM64 UI 包，输出 `firmware/U1_233_2.1.0-233-dev.3-ui.2_upgrade.bin`。专用 CI 只构建和校验原生运行包，不自动制作整包固件、不发布 Release、不刷机。

## 来源与许可

PAXX、multiACE 和 HelixScreen 的版权、来源与 GPL 许可均保留。233 是这个派生集成工程的名称；各组件的许可证与作者说明继续适用。参阅 [LICENSE](LICENSE)、[multiACE LICENSE](providers/multiACE/LICENSE)、[原生 UI 第三方材料](bundles/native-ui/licenses/README.md) 及 [PAXX 原始说明](README.upstream.md)。分发二进制时，需要一并提供对应源码、补丁、构建资料和相应许可要求的材料。

### 已验证的 233-dev.2-perms.1 基础 overlay

基础 overlay 以已安装的 `2.1.0-233-dev.1-rsyncfix.1` 包为输入，保留系统镜像中经过审查的离线 multiACE 包、版本及安装脚本；不会下载上游 multiACE，也不会读取或修改 `providers/multiACE` 的未提交工作。当前构建在它之后应用原生 UI overlay。默认输入文件为 `firmware/U1_233_2.1.0-233-dev.1-rsyncfix.1_upgrade.bin`；可使用 `FW233_BASE`、`FW233_OUTPUT` 指定路径。`./build-233.sh --plan` 显示构建顺序。

构建需要 Docker 和 `codex-233-overlay-tools:20261109` 镜像，构建定义见 `bundles/233-dev.2/Dockerfile`。已验证工具镜像 ID `sha256:b05ed54dd0b006fef266849fc3d11bfa73cfeb9db6f315127e0d2361d5f569cd`；容器内 Linux 文件系统展开 rootfs 并执行原有文件所有权检查。离线应用包的逐文件清单和来源见 `bundles/233-dev.2/manifest.json`；它是本次审查过的快照，后续 provider 修改不会自动进入固件。

安装必须先备份现有应用及用户配置，停止 Klipper 和 multiACE web，再升级应用、启动 Klipper 以重新绑定同一版本。旧应用目录保留供回滚。整包构建不代表已验证设备升级、启动或功能；设备升级路径须单独核对。

若现场 `ace_native_guard.py` 是指向旧包的符号链接，必须在 Klipper 停止后备份该链接并改成普通文件占位，再由启动 hook 绑定新包中的 guard；回滚旧系统时恢复原链接。不要对符号链接路径直接执行 bind mount，因它会跟随链接到旧包文件。升级前请验证现场类型，完整保留原对象以便回滚。

余量估算模块改编自 WatchingWatches/Filament-approximation 的 `Filament_approx.py`（Benedikt Jansson，源提交 `5ad5b3a1ce291a3f736bd2aa09756de5d23659d6`，MIT）。本次固件的伴随许可原文见 [bundles/233-dev.2/licenses/Filament-approximation.txt](bundles/233-dev.2/licenses/Filament-approximation.txt)，交付固件旁另有 `firmware/licenses/Filament-approximation.txt`。已构建的二进制和应用归档未因补充此伴随文件重新打包；后续归档再版应将许可纳入归档。

perms.1 在安装时将解包后的目录设为 0755，确保使用 umask 077 安装时 Klipper 用户仍可读取；文件权限和应用归档保持原样。已存在且版本号相同的应用不会自动重装；现场已有目录权限已修复。

ARM 实机安装器检查另发现：root 验证 Python 导入时可能新建权限为 0700 的 `__pycache__` 目录。现场普通用户导入已通过，但不能声称安装后每个目录都保持 0755；后续安装器修订需处理此问题。
