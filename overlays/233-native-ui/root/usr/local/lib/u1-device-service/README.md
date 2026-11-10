# U1 本机服务

机器端界面通过 `127.0.0.1:7136` 调用此服务。服务使用 Python 3 标准库，并复用本机 multiACE 网页后端、PAXX 配置接口与 U1 系统升级器。

## 启动

开发联调可运行 `python3 launch.py`。233 overlay 将本目录安装到 `/usr/local/lib/u1-device-service`，配套启动脚本为 `/etc/init.d/S58u1-device-service`。服务需要有权写入 `/userdata/u1-interface/firmware` 并调用 `/home/lava/bin/systemUpgrade.sh`。

本批是源码集成，尚未部署到设备；ARM64 运行包和整包固件需按仓库构建说明分别生成与验证。

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

在本目录运行 `python3 -m unittest discover -p 'test_*.py'`。测试中的升级执行器仅打印模拟日志；PAXX 测试使用随机本机端口上的假上游，验证认证转发、动态能力、HTTP 200 中的执行失败、读回一致性、断连不重试与打印/暂停时拒绝写入，不连接打印机。
