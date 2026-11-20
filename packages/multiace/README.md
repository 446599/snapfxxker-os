# 3. multiACE 包

`runtime/` 是当前主机侧应用的唯一编辑入口；版本只读取 `runtime/VERSION`。本轮为 `1.27b-233.20261120-flow.16`。

它控制 U1 主机上的 ACE 通信、进退料、缓冲助送料、RFID、余量估计、恢复与 Web 页面。它不包含 U1 原厂系统镜像，也不会自动刷写 ACE MCU。

## 来源

当前源码由 `629555c` 中的完整 Flow.13 运行源码，加上已推送 `a887123` 的 Flow.15 修复，再加本轮经过回归的修复组成。旧 `providers/multiACE` 是上游参考和早期候选，不能再作为默认运行包覆盖这里。

第三方 Python 依赖仍从固定的 `source-20261116` 快照读取，以便不在仓库中复制第二套 32 MB 依赖。构建器只取它的 `web/backend/vendor`，不会把旧 `ace.py`、旧界面或旧版本号覆盖回当前源码。

## 构建

```sh
# 完整主机应用包：需要仓库中完整的固定 vendor 依赖
python build.py multiace

# 仅代码更新包：不包含第三方 Python 依赖
python build.py multiace --code-only

# 指定输出位置
python build.py multiace --code-only --output dist/multiace-flow16-code.tar.gz
```

归档顶层为 `multiace-<VERSION>/`，内含 `PACKAGE.json` 与运行源码，旁边输出 SHA-256。`PACKAGE.json` 明确区分是否包含依赖、来源与验证范围。

**代码更新包需要已经安装好的 multiACE 依赖，不能拿它替换成一个空的新应用目录。** 完整包与代码更新包都不是打印机整机固件，也不是 ACE MCU 固件。当前没有新增自动部署行为。

## 分工与恢复原则

- 原厂已有的温度、运动和机器状态机制继续使用；不在 Web、屏幕、ACE 模块中重复建立同一套状态机。
- 没标签、余量未知、设备尚未就绪属于信息或可重试状态；不因此让 Klipper 内部错误关停。
- 正常失败或取消应结束当前操作并释放 busy 状态，保留真实料路；同一条错误不重复弹出。
- 已有自动恢复只做有限 STOP/读取复核；不因为不清楚料路就自动进退料。
- 无法确认电机停止时结束动作并使用原有恢复入口；不能吞掉 STOP 失败后继续挤出。

Flow.15 的缓冲控制与有限自动恢复已保留。本轮没有重写 MCU 运动控制，也没有把所有异常统一忽略。

## 验证

```sh
python -m unittest discover -s packages/multiace/tests -p 'test_*.py'
```

新增测试运行实际命令入口、读卡器和结果回调；外部电机、传感器和时钟由可控替身提供。原厂行为依据与本轮结果见 [修复记录](../../docs/maintenance/20261120-modular-recovery.md)。离线通过不等于已刷机或已完成实机连续换料验收。
