# U1 × ACE 进退料交接源码

版本对应 2026-11-16 的 [技术交接包](../U1-ACE进退料技术交接-20261116.zip)。U1 运行 `2.1.0-233-dev.3-ui.9`，multiACE 运行 `1.27b-233.20261116-flow.13`。ACE 2 Pro 的设备端版本为 `V1.2.01O`。这份源码用于阅读、复现上位机软件和重做进退料流程。

| 目录 | 内容 |
| --- | --- |
| [multiace/runtime](multiace/runtime/) | `flow.13` 软件包逐文件展开。`klipper/extras/ace.py`、`filament_feed_ace.py`、`ace_slot_actions.py`、`ace_protocol_v2.py` 分别涉及装退料调度、U1 喷头动作、槽口退出和 ACE 协议。目录还含配置、语言文件和网页。 |
| [multiace/rebuild](multiace/rebuild/) | 受校验的基础包、构建脚本、补丁和附加源文件。 [文件及包校验数据](multiace/manifest.json) |
| [u1-klipper](u1-klipper/) | 实机读取的 12 个 Klipper 源模块与现场有效宏。包含头部到料、换头、停靠、涡流线圈及弃料冲刷。设备端配套以原厂 Klipper 版本为准。 [Snapmaker U1 Klipper 上游](https://github.com/Snapmaker/u1-klipper) |
| [device-service](device-service/) | 实机运行的设备服务 Python 模块，包括插料 RFID 设置和空闲检查。 |
| [helixscreen](helixscreen/) | 原生界面 524 个本地改动/新增文件，含耗材联动、喷头控制、翻译和测试。上游与 14 个子模块按提交锁定，一条命令可叠加到干净检出。 [版本及源文件校验](helixscreen/manifest.json) |

## 重建 multiACE

在本目录运行。依赖 Python 3 和 `patch`。输出和清单写到工作目录之外。

```sh
python3 multiace/rebuild/build.py \
  --output /tmp/multiace-managed-flow.13.tar.gz \
  --manifest /tmp/multiace-managed-flow.13.json
shasum -a 256 /tmp/multiace-managed-flow.13.tar.gz
```

已实测重建结果与对应实机包逐字节一致，SHA-256 为 `a39fce7d180bbf26b9ee4b032dc4c22389e85a117e953abcdcc748e3429e46cd`。

工程树的 multiACE 离线回归复跑通过 367 项，跳过 2 项。

## 还原 HelixScreen

在本目录开始。首次获取上游和固定的子模块需要网络。构建使用 GNU Make，U1 目标使用项目的 Docker 工具链。

```sh
u1_handoff_source="$(pwd -P)"
git clone https://github.com/prestonbrown/helixscreen.git helixscreen-build
git -C helixscreen-build checkout --detach cf2e80ab6ccbf44b5ee389ffd0d68b4858ea9fd1
git -C helixscreen-build submodule update --init --recursive
python3 "$u1_handoff_source/helixscreen/apply-overlay.py" helixscreen-build
cd helixscreen-build
gmake apply-patches
gmake -n SKIP_OPTIONAL_DEPS=1
gmake snapmaker-u1-docker
```

已用干净检出和 14 个固定提交验证还原脚本、上游补丁和主机构建预检。四个关键界面源文件与上机时记录的 SHA-256 匹配，现机显示二进制校验为 `84c961e7e9be16b2cda458ec291f1e2475e00bbe38a8bf0d5bb446536ddc9c2c`。这次的 HelixScreen 验证到构建预检，ARM64 完整重编译仍待单独验证。

文件校验可从本目录运行 `shasum -a 256 -c SHA256SUMS`。版本化目录适合交接和复现，工程仓库原有固件构建入口照常使用各自的版本锁定。
