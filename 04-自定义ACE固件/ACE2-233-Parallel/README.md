# ACE 2 Pro 双槽并行候选固件

版本 **0.16.0-233a**。基于 ace2k v0.16.0 的每槽独立控制。一个槽保持跟随送料时，另一个槽可以执行指定距离的进料、退料或单槽停止。MCU 的协议字典提供 `ACE2K_LANES_CONCURRENT=1`，供 233 上位机识别。[上游版本](https://github.com/tobecwb/ace2k/releases/tag/v0.16.0)

这个镜像使用 ace2k 的 Klipper MCU 协议。233 的 MultiACE `.15.111` 需要接入对应的驱动后，才能在共用 Y 接头的打印模式里调用此能力。双槽动作适用于不同料路。共享的缓冲满信号仍由固件原有保护处理。[上游协议](https://github.com/tobecwb/ace2k/blob/v0.16.0/docs/protocol.md#the-shared-full-input)

本地通过了全部 1224 项 C 测试与 1,834 项 Python 测试。新增的双槽回归覆盖四槽的 12 种有序组合，每组检查有限进料和退料，共 24 种情况。实机联动、并行送料和换色验证待做。

## 重建

使用带 Newlib 的 Arm GNU Toolchain。本次镜像用 GCC 14.2.1 编译。请在路径无空格的目录检出源码。

```sh
git clone --branch v0.16.0 https://github.com/tobecwb/ace2k.git /tmp/ace2k-233a
cd /tmp/ace2k-233a
git submodule update --init firmware/klipper
git apply /absolute/path/parallel-lanes.patch
make -C firmware flag-check ACE2K_VERSION=0.16.0-233a
make -C firmware host-bundle ACE2K_VERSION=0.16.0-233a
make -C firmware test-c
```

上游源码固定在 `27c564d210170f01aa1a57cd928a60c520247aff`，Klipper 固定在 `2d7717e3b62ea2fe3401b27f54f8681f80451c69`。
