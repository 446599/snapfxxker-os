# 1. 原厂固件

这里保存原厂镜像的来源、版本、校验和与校验入口。原厂 `.bin` 保持字节不变；屏幕、multiACE 和 ACE MCU 的修改分别构建。

当前工程基线为 `2.1.0.157`，固定值来自仓库的 `vars.mk`。这不是对“最新官方版本”的宣称。下载地址和 SHA-256 见 [manifest.json](manifest.json)。

```sh
python build.py stock --plan
python build.py stock --input /path/to/U1_2.1.0.157_20261028045538_upgrade.bin
```

可额外传 `--output dist/stock/<文件名>.bin` 创建完全相同的副本。脚本不联网下载、不改镜像、不刷机。

## 原厂与已有 233 系统的关系

`233-firmware.json` 中的 `2.1.0-233-dev.1-rsyncfix.1` 是已经修改过的 233/PAXX 系统基线，不能把它标成原厂固件。旧 `build-233.sh` 仍用于历史整包复现。

四层拆分描述的是源码和交付边界，并不表示原厂裸系统已提供扩展应用管理器。屏幕和 multiACE 的现有安装桥接仍依赖 233/PAXX 的 Klipper、Moonraker、服务与应用目录。不要把单独应用包直接当成整机升级 `.bin`。

## 行为参照

进退料状态及错误处理对照 [Snapmaker 官方 2.1.0 源码](https://github.com/Snapmaker/u1-klipper/tree/7ac453b50fe6779b2ec0b759038a17da5e6d671d)。本轮按它恢复了自动模式切换时的已装料状态、完整退料后的主状态收尾，以及普通命令错误与内部程序故障的区分。
