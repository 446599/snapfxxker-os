# U1 本机服务

机器端界面通过 `127.0.0.1:7136` 调用此服务。服务使用 Python 3 标准库，并复用本机 multiACE 网页后端与 U1 系统升级器。

## 启动

开发联调可运行 `./run-service.sh`。机器端将本目录安装到 `/opt/u1-interface/device-service`，再安装配套的 `S58u1-device-service` 到 `/etc/init.d`。服务需要有权写入 `/userdata/u1-interface/firmware` 并调用 `/home/lava/bin/systemUpgrade.sh`。

本次交付保留为本地安装包，设备部署由机器端测试窗口安排。

## 本地升级

- 支持本机 U1 的完整 `.bin` 升级包。
- 从 `/media`、`/mnt`、`/run/media` 与 `/userdata/firmware` 扫描文件，最多显示 128 个文件，扫描四级目录。
- 选择后复制到本机暂存，校验 SNMK 包头、四个分区、可选扩展分区、边界、重叠与 MD5，并计算整个包的 SHA-256。
- 确认使用一次暂存令牌，并在启动前再次核对 SHA-256 与打印机空闲状态。
- 执行 `/home/lava/bin/systemUpgrade.sh upgrade all <本机暂存包>`。
- 进程输出持续写入 `/userdata/u1-interface/firmware/upgrade.log`。状态保存为同目录的 `status.json`。
- 写入完成进入等待重启状态。检测到新的系统启动后显示当前 `/etc/FULLVERSION`，由操作人核对实际版本。

分区校验遵循 233 固件仓库 `tools/upfile/upfile.c` 与 `helpers.h` 的格式。界面显示的校验结果表示文件完整性。

## 耗材功能

`/state` 与 `/spoolman/sync` 复用本机 `127.0.0.1:7126` 的 multiACE 网页后端。耗材配置与网页共用同一份设备数据。装退料沿用原生 AMS 后端，耗材库和设置操作由命令提供模块转换为与网页相同的宏。

## 检查

在本目录运行 `python3 -m unittest discover -p 'test_*.py'`。测试中的升级执行器仅打印模拟日志。
