# U1 本机服务

机器端界面通过 `127.0.0.1:7136` 调用此服务。服务使用 Python 3 标准库，并复用本机 multiACE 网页后端、PAXX 配置接口与 U1 系统升级器。

## 启动

开发联调可运行 `python3 launch.py`。233 overlay 将本目录安装到 `/usr/local/lib/u1-device-service`，配套启动脚本为 `/etc/init.d/S58u1-device-service`。服务需要有权写入 `/userdata/u1-interface/firmware` 并调用 `/home/lava/bin/systemUpgrade.sh`。

本批是源码集成，尚未部署到设备；ARM64 运行包和整包固件需按仓库构建说明分别生成与验证。

### 系统槽位与回退

`[u1_native_upgrade]` 只保存在当前系统镜像的 `/usr/local/share/u1-native-upgrade/klipper.cfg`，与该镜像中的 Klipper 模块一起生效。共享的 `/oem/printer_data/config/extended/klipper/u1_native_upgrade.cfg` 仅包含可选的 `[include /usr/local/share/u1-native-upgrade/*.cfg]`。旧 `perms.1` 系统中没有匹配文件，固定版本的 Klipper 加载器允许通配 include 为空，因此不会尝试导入新模块。

Klipper 启动钩子在维护锁检查通过后运行 `slot_config.py`。它会将 `1fdd0ac` 写入的无选项旧配置段迁移为通配 include，保留其他配置段、注释、权限及所有者，原内容备份为不匹配 `*.cfg` 的文件。重复启动不重写已有正确配置。未知配置项、符号链接或非普通文件会保留原对象并报告错误；迁移不会修改主 `printer.cfg`。

## 本地升级

- 支持本机 U1 的完整 `.bin` 升级包。
- 从 `/media`、`/mnt`、`/run/media` 与 `/userdata/firmware` 扫描文件，最多显示 128 个文件，扫描四级目录。
- 选择后复制到本机暂存，校验 SNMK 包头、四个分区、可选扩展分区、边界、重叠与 MD5，并计算整个包的 SHA-256。
- 确认使用一次暂存令牌，启动前先重新计算 SHA-256，再读取完整实时状态。`prepare` 和状态接口包含 `scope`：`u1-all` 明确表示 **U1 主机系统、主控 MCU 和四个工具头 MCU**；此入口不更新 ACE 固件。
- 空闲检查覆盖打印/暂停/运动、ACE 槽位动作、换料、卸料、标签操作、料路位置锁、校准与校准退料、后台换料、原厂送料器装退料、每台 ACE 的固件更新占用和烘干/料盘旋转，以及动态发现的全部喷头、热床和舱温加热器。加热器目标或输出功率非零均拒绝。没有配置 ACE 时可通过；已发现的对象、加热器或 ACE 状态不完整时拒绝。
- 设置、耗材同步、固件准备和升级共用非阻塞操作锁。升级工作线程持有该锁直到执行器退出，其他写请求立即返回忙，不排队等候升级后执行。
- 工作线程建立 `/run/u1-native-upgrade/request.json` 维护锁，然后停止 Moonraker 并确认原 PID 已退出。Klipper 与 Moonraker 的启动钩子在维护锁存在时拒绝额外 `start/restart`，阻止 PAXX 或其他服务在交接中重新开启控制入口。
- 服务通过 Klipper 的本机 Unix socket 调用 `u1_upgrade/prepare`。独立 Klipper 模块在 reactor 同一回调中取得非阻塞 G-code 互斥锁，直接读取并检查完整状态，写入绑定本次随机令牌及原 PID 的收据，然后立即退出进程。这避免另一个 G-code 请求或自动烘干定时器插入“末次检查到退出”之间。服务只在验证收据且确认原 Klipper PID 已退出后继续；没有模块、无效收据或未退出均不会调用执行器。
- ACE 的独立串口刷写需先设置 `fw_hold`，最终原子检查会拒绝该状态；Moonraker 已退出后无法发起新的 `ACE_FW_RELEASE`。因此只在此检查及 Klipper 退出成功后才停止 multiACE 网页进程并确认退出。
- 控制进程交接不提前切断 MCU 电源，保留原升级器读取 MCU 型号和实施自身电源时序的能力。随后执行 `/home/lava/bin/systemUpgrade.sh upgrade all <本机暂存包>`。
- 进程输出持续写入 `/userdata/u1-interface/firmware/upgrade.log`。状态保存为同目录的 `status.json`。
- 在执行器成功启动前失败，会先撤销本次维护锁，再按依赖顺序恢复本次停止的原有服务。执行器已经启动后发生错误则进入 `recovery_required`，保留维护锁，不自动重启控制服务或允许第二次写入；日志和版本需要人工核对。维护锁位于 `/run`，整机重启时清除。
- 写入完成进入等待重启状态。检测到新的系统启动后显示当前 `/etc/FULLVERSION`，由操作人核对实际版本。

分区校验遵循 233 固件仓库 `tools/upfile/upfile.c` 与 `helpers.h` 的格式。界面显示的校验结果表示文件完整性。

## 耗材功能

`/state` 与 `/spoolman/sync` 复用本机 `127.0.0.1:7126` 的 multiACE 网页后端。耗材配置与网页共用同一份设备数据。装退料沿用原生 AMS 后端，耗材库和设置操作由命令提供模块转换为与网页相同的宏。

## PAXX 设置

### 连接与认证

服务通过本机 nginx 的 `http://127.0.0.1/firmware-config/api/` 调用已安装 PAXX 的原配置后端。高级模式及 Firmware Config 服务需要已开启。服务不会直连内部 `9091` 端口，也不会在认证失败后尝试绕过认证。

启动服务时可通过环境变量提供已有凭据：

| 环境变量 | 作用 |
| --- | --- |
| `U1_PAXX_API_KEY` | 转发为 `X-Api-Key`，同时用于打印状态读取 |
| `U1_PAXX_TOKEN` | 转发为 `Authorization: Bearer …`；令牌需要在有效期内 |
| `U1_PAXX_API_URL` | 默认 `http://127.0.0.1/firmware-config/api`；仅接受本机地址和该 nginx 路径，可指定自定义 nginx 端口 |

凭据不随安装包提供，配置响应不返回环境变量。服务只监听 `127.0.0.1:7136`。认证仍由 PAXX/Moonraker 检查；没有凭据且上游要求登录时，界面会显示登录错误。

PAXX 与通用本机 JSON 请求共用显式 `ProxyHandler({})` 的私有 HTTP opener，回环流量不会继承大小写 `HTTP_PROXY`、`HTTPS_PROXY` 或 `ALL_PROXY`。无需依赖启动环境正确配置 `NO_PROXY`；现有认证头仍原样转发。

### 读取接口

`GET /firmware/settings` 返回：

```json
{
  "available": true,
  "source": "paxx",
  "groups": [{"id": "materials", "label": "耗材与识别"}],
  "settings": [{
    "id": "spoolman",
    "group": "materials",
    "source_group": "snapmaker_components",
    "label": "Spoolman 耗材库",
    "source_label": "Spoolman Integration",
    "description": "…",
    "help_url": "…",
    "current": "disabled",
    "hidden": false,
    "requires_idle": true,
    "options": [{
      "value": "enabled",
      "label": "启用",
      "source_label": "Enabled",
      "confirm": "Enable Spoolman?",
      "hidden": false,
      "inputs": [{
        "name": "SPOOLMAN_URL",
        "label": "Spoolman 服务器地址",
        "source_label": "Spoolman URL",
        "type": "text",
        "required": true,
        "placeholder": "http://192.168.1.100:7912",
        "regex": "^https?://.+"
      }]
    }]
  }]
}
```

这是结构示例，不是固定设备能力清单。每次请求直接读取已安装 PAXX 返回的设置、选项、条件可见性、确认说明与输入定义。新 UI 将已知项目归入 `printing`、`materials`、`camera`、`motion`、`remote`、`interface`、`maintenance`、`updates` 八组；未识别的项目保留上游名称，放在对应原分类或“界面与扩展”。不会凭文档向设备添加不存在的选项。

`hidden` 元数据保留供客户端识别，隐藏项目和隐藏选项不得呈现为可操作设置，写接口也会拒绝。上游的布尔选项值为字符串 `"true"` / `"false"`；客户端应原样回传 `value`。当前 PAXX 的额外输入均为必填文本字段，包含 `name`、`label`、`placeholder`、可选 `regex`，本服务为缺省类型补充 `type: text` 和 `required: true`，并保留其他字段。

### 单项应用接口

`POST /firmware/settings`，`Content-Type: application/json`：

```json
{
  "id": "spoolman",
  "value": "enabled",
  "confirm": true,
  "inputs": {"SPOOLMAN_URL": "http://192.168.1.100:7912"}
}
```

仅选项含上游确认文案时要求 `confirm: true`。客户端需要先显示该说明；附加输入名和值按上游定义提交。未声明的输入、隐藏设置、无效选项、缺失或格式不符的必填项会在写入前拒绝。

PAXX 的设置元数据没有声明各项重启范围。为避免在打印或暂停时重启 Klipper，当前版本对所有 PAXX 配置写入复用现有 `machine_idle()` 检查：打印机须 ready、没有打印/暂停/运动及进行中的 ACE 操作。读取不受此限制。升级任务与设置修改共用操作锁。

设置写入沿用 PAXX 原动作，包括配置文件维护、组件下载安装和必要的服务/整机重启。本服务不直接修改 `extended2.cfg`、不批量提交，也不自动重试写入。

| 返回结果 | UI 处理 |
| --- | --- |
| HTTP 200，`ok: true, state: applied` | 上游明确完成且当前值读回一致；可显示“已应用” |
| HTTP 200，`ok: false, state: failed` | 上游操作流包含 `ERROR`；显示 `message`，可查看 `error` 与 `output` |
| HTTP 200，`ok: false, state: unverified` | 写入连接中断、无完成标记或读回不一致；显示“等待确认”，重连后刷新，不自动重新提交 |
| HTTP 400，`error` | 写入前校验、认证、设备忙或接口请求失败；显示原因 |

PAXX 操作流即使 HTTP 200 也可能执行失败，所以客户端必须检查 `ok/state`。成功响应含 `current` 和归一化后的 `setting`。操作结果中的 `output` 保留本次执行详情，可能含 Tailscale 登录链接、账号绑定说明或新生成的登录凭据：仅在屏幕执行详情中显示，不写普通日志。本服务不把这些输出持久化。

上游读取超时为 20 秒，写入流等待为 180 秒；下载组件可能较慢。原生客户端应异步等待并保持应用状态，可使用较长请求超时。客户端超时或整机重启后应重新读取实际值，不能将断线解释为已生效。

### 对接依据

API 路径、动态 schema、文本流完成标记来自作者仓库的 `firmware-config.py`，认证路径来自同目录组件的 nginx 配置：

- https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware/blob/develop/overlays/firmware-extended/02-firmware-config/root/usr/local/bin/firmware-config.py
- https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware/blob/develop/overlays/firmware-extended/02-firmware-config/root/etc/nginx/fluidd.d/firmware-config.conf

核对时源码版本为 `7f435c38e011c8f5e24512f617d3bdfacb65b881`。设备安装版本仍以运行时返回的能力为准；此适配未连接实机验证。

## 检查

在本目录运行 `python3 -m unittest discover -p 'test_*.py'`。`test_upgrade_guard.py` 使用离线状态快照、假进程与实际线程交错验证遗漏状态、动态加热器、检查后出现槽位/OTA 操作时拒绝刷写、收据/令牌校验、执行器启动前恢复、启动后保留维护锁，以及所有服务写接口的并发互斥。Klipper 模块的测试还验证独立子进程退出后不会执行同轮定时器。测试中的升级执行器仅打印模拟日志；PAXX 测试使用随机本机端口上的假上游，验证认证转发、动态能力、HTTP 200 中的执行失败、读回一致性、断连不重试与打印/暂停时拒绝写入，不连接打印机。
