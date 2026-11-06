# 233固件

面向 **Snapmaker U1 + Anycubic ACE** 的集成固件工程，以 PAXX Extended Firmware 为底座，集成 multiACE 的 ACE 控制、耗材管理与 Web 界面。

当前开发版本：`2.1.0-233-dev.1`。本次已完成源码拉取、依赖固定及项目命名，尚未编译固件、刷写打印机或发布 Release。

## 源码基线

| 组件 | 上游 | 当前固定版本 |
| --- | --- | --- |
| PAXX 扩展固件 | [SnapmakerU1-Extended-Firmware](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware) | develop / `bb1fd3705ec9608d71122462b07556613dbbbb1f` |
| multiACE | [decay71/multiACE](https://github.com/decay71/multiACE) | v1.27b / `a1a7b91b1fd297c1486a13ea22c61e01499a196d` |
| screen-apps | [paxx12/screen-apps](https://github.com/paxx12/screen-apps) | PAXX 指定的 `4a81b50d9b6ef7a2c253b45dd00fe6f732c9d203` |
| 原厂基础包 | Snapmaker U1 | `2.1.0.157`，校验和见 [233-firmware.json](233-firmware.json) |

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

该入口沿用 PAXX 的 Docker 构建环境和 `extended-multiace` 配置，将 `GIT_VERSION` 设置为 `2.1.0-233-dev.1`，约定输出文件为：

```text
firmware/U1_233_2.1.0-233-dev.1_upgrade.bin
```

**目前这个文件尚未生成。** PAXX 原有流程仍按固定 URL 与 SHA-256 按需下载 managed multiACE 包，`providers/multiACE` 的本地改动尚未接入镜像打包。把本地 provider 构建、依赖与安装流程统一到 233 中，是下一步的重构任务，见 [集成计划](docs/233/INTEGRATION_PLAN.md)。

## 来源与许可

PAXX 和 multiACE 的版权、来源与 GPL-3.0 许可均保留。233 是这个派生集成工程的名称；各组件的许可证与作者说明继续适用。参阅 [LICENSE](LICENSE)、[multiACE LICENSE](providers/multiACE/LICENSE) 及 [PAXX 原始说明](README.upstream.md)。
