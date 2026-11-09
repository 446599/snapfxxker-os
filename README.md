# 233固件

面向 **Snapmaker U1 + Anycubic ACE** 的集成固件工程，以 PAXX Extended Firmware 为底座，集成 multiACE 的 ACE 控制、耗材管理与 Web 界面。

当前开发版本：`2.1.0-233-dev.2-perms.1`。此修订已部署并启动验证，Klipper 和 ACE 显示 ready，运行模块与数据核对一致；本次未进行新的运动或加热测试，也未发布 Release。上一版 `2.1.0-233-dev.2` 的现场目录权限已修复。构建基线为现场已安装的 `2.1.0-233-dev.1-rsyncfix.1`。

## 源码基线

| 组件 | 上游 | 当前固定版本 |
| --- | --- | --- |
| PAXX 扩展固件 | [SnapmakerU1-Extended-Firmware](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware) | develop / `bb1fd3705ec9608d71122462b07556613dbbbb1f` |
| multiACE | [decay71/multiACE](https://github.com/decay71/multiACE) | v1.27b / `a1a7b91b1fd297c1486a13ea22c61e01499a196d` |
| screen-apps | [paxx12/screen-apps](https://github.com/paxx12/screen-apps) | PAXX 指定的 `4a81b50d9b6ef7a2c253b45dd00fe6f732c9d203` |
| 系统基线 | 233 现场 rsyncfix 包 | `2.1.0-233-dev.1-rsyncfix.1`；原厂上游为 `2.1.0.157`，校验和见 [233-firmware.json](233-firmware.json) |

主仓库和 multiACE 工作分支均为 `codex/233-firmware`。主仓库的 `upstream` 指向 PAXX，目前未设置 233 自己的发布远端。

multiACE 上游 main 在拉取时为 `1.50-pre`，其代码和历史已拉取并保留在 `providers/multiACE` 的 `origin/main`。初始集成锁定 PAXX 当前使用的 v1.27b；升级预发布版本需要单独评估接口、配置与 managed 包兼容性。

## 工程布局

```text
233-firmware/
├── 233-firmware.json        项目名称、开发版本、上游提交与包校验和
├── build-233.sh             233 命名的构建入口
├── overlays/               PAXX 基础功能及已有 multiACE 集成
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

以后实际构建使用：

```sh
./build-233.sh
```

该入口使用已校验的现场 rsyncfix 基线和本地离线 multiACE 快照构建 `firmware/U1_233_2.1.0-233-dev.2-perms.1_upgrade.bin`。具体环境和安装边界见下文；旧版 PAXX 上游构建流程可见 [README.upstream.md](README.upstream.md)。

## 来源与许可

PAXX 和 multiACE 的版权、来源与 GPL-3.0 许可均保留。233 是这个派生集成工程的名称；各组件的许可证与作者说明继续适用。参阅 [LICENSE](LICENSE)、[multiACE LICENSE](providers/multiACE/LICENSE) 及 [PAXX 原始说明](README.upstream.md)。

### 233-dev.2-perms.1 离线集成构建

`./build-233.sh` 以已安装的 `2.1.0-233-dev.1-rsyncfix.1` 包为输入，校验 SHA-256 后仅覆盖系统镜像中经过审查的离线 multiACE 包、版本及安装脚本；不会下载上游 multiACE，也不会读取或修改 `providers/multiACE` 的未提交工作。默认输入文件为 `firmware/U1_233_2.1.0-233-dev.1-rsyncfix.1_upgrade.bin`；可使用 `FW233_BASE`、`FW233_OUTPUT` 指定路径。`./build-233.sh --plan` 显示构建计划。

构建需要 Docker 和 `codex-233-overlay-tools:20261109` 镜像，构建定义见 `bundles/233-dev.2/Dockerfile`。已验证工具镜像 ID `sha256:b05ed54dd0b006fef266849fc3d11bfa73cfeb9db6f315127e0d2361d5f569cd`；容器内 Linux 文件系统展开 rootfs 并执行原有文件所有权检查。离线应用包的逐文件清单和来源见 `bundles/233-dev.2/manifest.json`；它是本次审查过的快照，后续 provider 修改不会自动进入固件。

安装必须先备份现有应用及用户配置，停止 Klipper 和 multiACE web，再升级应用、启动 Klipper 以重新绑定同一版本。旧应用目录保留供回滚。整包构建不代表已验证设备升级、启动或功能；设备升级路径须单独核对。

若现场 `ace_native_guard.py` 是指向旧包的符号链接，必须在 Klipper 停止后备份该链接并改成普通文件占位，再由启动 hook 绑定新包中的 guard；回滚旧系统时恢复原链接。不要对符号链接路径直接执行 bind mount，因它会跟随链接到旧包文件。升级前请验证现场类型，完整保留原对象以便回滚。

余量估算模块改编自 WatchingWatches/Filament-approximation 的 `Filament_approx.py`（Benedikt Jansson，源提交 `5ad5b3a1ce291a3f736bd2aa09756de5d23659d6`，MIT）。本次固件的伴随许可原文见 [bundles/233-dev.2/licenses/Filament-approximation.txt](bundles/233-dev.2/licenses/Filament-approximation.txt)，交付固件旁另有 `firmware/licenses/Filament-approximation.txt`。已构建的二进制和应用归档未因补充此伴随文件重新打包；后续归档再版应将许可纳入归档。

perms.1 在安装时将解包后的目录设为 0755，确保使用 umask 077 安装时 Klipper 用户仍可读取；文件权限和应用归档保持原样。已存在且版本号相同的应用不会自动重装；现场已有目录权限已修复。

ARM 实机安装器检查另发现：root 验证 Python 导入时可能新建权限为 0700 的 `__pycache__` 目录。现场普通用户导入已通过，但不能声称安装后每个目录都保持 0755；后续安装器修订需处理此问题。
