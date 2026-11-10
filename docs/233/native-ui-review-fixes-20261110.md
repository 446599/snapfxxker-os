# U1 原生 UI：df0f5c3 审查修复

审查基线：`df0f5c334dcda6adb91a2931b3175f5a01e5aaa7`。修复分支：`codex/u1-native-ui-20261110`。源码修订：`2.1.0-233-dev.3-ui.3`，运行包标识 `233-native-ui-20261110.2`。

本文件记录首次修复提交 `1fdd0ac` 的验证结果。后续发现的系统槽位回退问题与修复见 [回退修复记录](native-ui-slot-rollback-20261110.md)；当前交付文件见 [产物索引](native-ui-20261110-artifacts.md)。

本轮仅修改源码并进行离线验证，没有连接、重启或刷写打印机。修复从远端原提交的文件恢复，逐个核对 Git blob；没有使用 Mac 隔离工作区中的未提交草稿。

## 1. P1：升级状态与操作互斥

`device_service.py` 与新共享模块 `idle_checks.py` 按已固定的 multiACE 实现读取实际状态字段。除打印、暂停、运动外，检查 ACE 槽位动作、换料、卸料、标签操作、位置锁、校准、后台换料、原厂送料器、每台 ACE 的烘干和旋转、ACE 固件更新占用，以及动态发现的全部喷头、热床、舱温加热器。加热器目标和输出功率必须均为零。没有配置 ACE 可正常检查；已发现设备但状态不完整则拒绝。

耗时的整包 SHA-256 计算完成后才执行实时检查。所有本机写接口使用非阻塞操作锁，升级期间不会将其他写请求排队到升级结束后执行。

仅加 Python 锁无法阻止原生界面或网页直接向 Moonraker 发命令，因此增加独立 Klipper 模块 `u1_native_upgrade.py` 与两个启动钩子：

1. 在停止控制接口前建立 root 拥有的维护请求。Klipper、Moonraker 的 init hook 在维护请求存在时拒绝额外启动和重启。
2. 停止 Moonraker，并确认原进程已经退出。
3. 通过现有 Klippy Unix socket 调用升级入口。在同一个 reactor 回调内检查 G-code 互斥锁和完整设备状态；通过后写入绑定随机令牌和原 PID 的收据，并立即退出 Klippy。
4. 服务同时验证收据及原进程退出，再停止 multiACE 网页进程。ACE 的直接串口刷写需要 `fw_hold`，该状态会在前一步阻断升级。
5. 最后调用原有 `/home/lava/bin/systemUpgrade.sh upgrade all`。交接阶段不提前切断 MCU 电源，保留原升级器读取 MCU 型号及控制电源时序的能力。

原子交接使用升级专用的 `os._exit(0)`：固定 Klipper 版本的正常 reactor 退出仍可能执行同一批剩余 timer，而 multiACE 自动烘干可以由 timer 直接发出。因此只有完整空闲检查通过后才采用立即退出，并要求进程退出证明。这个路径不执行普通 disconnect 清理；后续 MCU 电源和重启由原升级器负责。这属于代码和进程模拟层验证，尚无真实设备交接验收。

执行器尚未成功启动时失败，撤销本次维护请求并恢复本次停止、原先运行的服务；后台启动后有界等待 PID 和进程标识就绪。执行器启动后失败，保留维护请求并进入 `recovery_required`，防止自动重启控制通道或再次写入。

确认页及接口明确显示：**更新 U1 主机系统、主控 MCU 和四个工具头 MCU；不更新 ACE 固件。**

## 2. P1：字体补丁链

固定 HelixScreen 基线仍是 `cf2e80ab6ccbf44b5ee389ffd0d68b4858ea9fd1`。原补丁 4 将未记录的中间字体重建当作预像，导致 35 个字体的二进制差分无法接在补丁 1–3 后应用。

重新生成补丁 4 的字体段，39 个非字体代码段逐字保留。首个字体的正确变化为：

```text
20fcbd02ff295e32e0a85206e4df35ad97591da8
  -> 7922dbcfb516cfdddf0f77d51cc72feffc4aeecd
```

新增补丁 5，包含升级范围文案，以及新增服务提示对应的字体和字符清单。恢复源码时，Klipper 升级模块也复制到字体扫描输入目录，确保其错误信息被扫描。

所有 5 个补丁均已从固定干净基线逐一执行 `git apply --check --index` 和实际应用。使用固定 `lv_font_conv@1.5.3` 和校验一致的两份 Noto OTF 完整重建 35 个字库；最终 1,361 个所需字符全部覆盖。恢复树与实际重建的 35 个字体逐个二进制一致。新增离线回归对原补丁产生 35 处失败，对修复后的链通过。

## 3. P2：本机请求绕过环境代理

两个本机 HTTP 请求助手改用私有 `build_opener(ProxyHandler({}))`。不修改进程全局 opener，不移除认证头，不要求调用者清除代理环境变量。

新增测试启动真实 loopback HTTP 目标和返回 502 的假代理，同时设置大小写代理变量并清空两种 `NO_PROXY`，验证两个助手的 GET、POST、JSON 与认证头均到达目标，代理收到的请求数为零。相同测试对原服务可复现 502。

## 验证

| 检查 | 结果 |
| --- | --- |
| 安装、来源指纹、补丁链、Klipper 原子交接与启动钩子 | 28 项通过 |
| 设备服务、升级状态/并发/恢复、代理环境 | 39 项通过 |
| 从干净固定基线应用补丁 1–5 | 全部通过 |
| 固定 Git 子模块 | 14 个提交逐一匹配 |
| XML 语法与 schema | 484 个布局，0 错误、0 警告 |
| 完整 CJK 字体重建 | 35 个字体，1,361 个所需字符，缺字 0 |
| 干净源码 U1 ARM64 编译 | 主界面、启动程序、守护程序均编译成功，AArch64 ELF |
| 运行包导入及完整校验 | 1,080 个文件、484 个 XML、35 个字体；权限、架构、逐文件哈希及构建指纹通过 |

测试中的升级器、控制服务和设备状态均为模拟对象；原子退出测试使用隔离子进程验证收据与退出后 timer 不执行。原有 multiACE 和 screen-apps 提交、managed 包 SHA-256 保持不变。

本轮构建使用清洁的固定上游与 14 个实际 Git 子模块检出、重新应用补丁 1–5、校验过的 Debian 交叉工具链和锁定字体转换器。编译结束码为 0；上游弃用警告保留，未出现编译错误。运行归档已在临时工作区实际执行 `package_runtime.py`、`pin_runtime.py` 与完整 `validate.py`，源码提交继续保持 runtime pin 为空，避免把未分发的二进制当作仓库内产物。

构建输入指纹：`4aadedc2ac2d7fad22b32109239f84dc03910267e45f1d655fab5385acb80a9e`。
主程序 SHA-256：`85b39780c21abd82ee0f9684dbca6071f112d01950697957dfb71abb4838bfca`。

### 复现入口

```sh
python3 scripts/native-ui/validate.py --source-only
python3 -m unittest discover -s tests/native-ui -v
python3 -m unittest discover \
  -s overlays/233-native-ui/root/usr/local/lib/u1-device-service -p 'test_*.py' -v
scripts/native-ui/build.sh /tmp/u1-ui3-build
```

CI 已将快速回归移到原生编译之前。升级模块、两个启动钩子及共享服务源码都纳入构建输入指纹；旧运行包不能通过本修订的导入校验。

## 首次修复提交的产物边界

首次修复提交 `1fdd0ac` 没有部署，也没有生成新的整机 `.bin`；当时提供的旧 `ui.2` 文件对应 `df0f5c3`，不包含本记录中的修复。后续完整包及其对应输入指纹以当前 [产物索引](native-ui-20261110-artifacts.md) 为准。
