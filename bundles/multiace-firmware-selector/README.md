# MultiACE flow.15.113 固件选择组件

本组件从整机版本 `2.1.2-233-paxx8d8af0d.2` 内嵌的 flow.15.112 运行包重建。包内保留原来的 V1.2.04O 镜像，并加入该版本发布页单独提供的 `ace2k-0.16.0-233b.bin`。网页和设备服务改动记录在 `runtime.patch`。

### 使用网页刷写

装有 flow.15.113 并已加载配套 Klipper 模块的 U1，在打印机空闲时打开「MultiACE → 配置 → ACE 固件」。每个已识别的二代料盒有两个按钮。确认框显示所选料盒和目标固件。

| 目标 | 随包镜像 |
| --- | --- |
| ACE2-Open V1.2.04O | `ACE2-1.2.04O-insert-policy.bin` |
| ace2k 0.16.0-233b | `ace2k-0.16.0-233b.bin`（220V/50Hz 版） |

这两项适用于使用 v2 或 ace2k 协议的 ACE 2 Pro。一代 ACE Pro 不进入该列表。每次更新选中的一个串口。跨协议切换会更新这个端口的驱动映射；完成状态要求重连后在同一端口读到目标版本。从 v2 切换至 ace2k 时，上游工具在 `printer_data/config/extended/multiace/ace-calibration/` 保存读取的出厂校准数据。每个适配器端口应仅连接一个 ACE，等该设备的烘干和进退料结束后再更新。

### 在固件中集成

已构建的 .113 组件与对应 `extended-pkg/multiace` 描述符在两个现行 overlay 中成对提供。集成到基于 .2 的固件时，将选定 overlay 的 `usr/local/share/233-firmware/multiace-managed-1.27b-233.20261122-flow.15.113.tar.gz` 及同一 overlay 的 `usr/local/share/extended-pkg/multiace` 带入目标系统，使用该版本的包管理器安装组件并重新加载 MultiACE 网页服务和 Klipper 模块。现有 .2 的描述符固定为 .112，单纯刷新网页或对旧系统执行 `extended-pkg multiace upgrade` 仍会使用 .112。

### 离线重建

在具备 Python 3.12 和 GNU patch 的构建机准备三个输入。

| 输入 | 来源 |
| --- | --- |
| `multiace-managed-1.27b-233.20261122-flow.15.112.tar.gz` | 已发布整机镜像中的 `/usr/local/share/233-firmware/`，可从该版本的只读系统复制。 |
| `ace2k-0.16.0-233b.bin` | [U1 2.1.2-233-paxx8d8af0d.2 发布附件](https://github.com/446599/snapfxxker-os/releases/tag/v2.1.2-233-paxx8d8af0d.2) |
| `ace2k_flash.pyz` | [ace2k v0.16.0 官方发布附件](https://github.com/tobecwb/ace2k/releases/tag/v0.16.0) |

在仓库根目录执行以下命令。示例将三个输入放在 `inputs/`。

```sh
python3 bundles/multiace-firmware-selector/build.py \
  --base inputs/multiace-managed-1.27b-233.20261122-flow.15.112.tar.gz \
  --ace2k inputs/ace2k-0.16.0-233b.bin \
  --flasher inputs/ace2k_flash.pyz \
  --output-dir dist/
```

构建脚本校验三个输入的 SHA-256，生成 `multiace-managed-1.27b-233.20261122-flow.15.113.tar.gz` 并打印校验值。两个现行 overlay 使用同名同内容的归档，分别供 PAXX multiace 配置和 233 集成构建使用。PAXX 的 `extended-pkg/multiace` 通过版本、文件路径和 SHA-256 加载随固件分发的包。历史 `233-dev.2-perms.1` 的 single.5 构建拥有独立版本锁定。

组件包包含网页、运行时及其依赖。组件构建在本地运行，不连接打印机。上游刷写工具、固定版本和许可证随包提供，来源见 [ace2k-flasher-NOTICE](ace2k-flasher-NOTICE)。


### 离线测试

在有 FastAPI、httpx、websockets、python-multipart 的 Python 3.12 构建机运行下列命令。用例直接从仓库内 .113 归档读取生产代码，以模拟的 Moonraker 和串口状态验证网页请求、设备选择、版本回读及协议映射。

```sh
python3 -m unittest discover -s bundles/multiace-firmware-selector/tests
```
