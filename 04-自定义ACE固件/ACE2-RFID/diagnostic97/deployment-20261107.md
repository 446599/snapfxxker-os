# 97O 实机部署记录 · 2026-11-07

ACE0 UID 808780566/17445170/942880330，95O→97O 使用原 OTA 库（SHA256 c67395d7299bb0de82944a13a2b659043fc6f25696c1951f19d1de3757199f1f）；镜像 SHA256 3863df3742bf23f9b7c8ae297be5ddaeb1b0eac423ce1a7ac574f96429d5d853，78898 bytes，传输 CRC（FFFF初值）0x159D。dry-run 通过，写入返回 verified=true、app_alive=true；fresh DISCOVER/INFO 确认同UID/97O。95O 回退包 SHA256 487b229840d65acc388926e9bae5502d12fdcd1b99372012b7461e6d24a3d26a。详细日志在 `../diagnostic95/ota97-prep/`。

随后为原生预装验证建立本机监督：等待300秒未观察到拔出或插入，等待超时后STOP并confirmed，**没有97原生预装样本**，见 `../diagnostic95/native-guard/run-20261107-97/`。取消后恢复原配置及协议，临时extra/include移除；fresh同UID/97O、printer ready、四槽ready、四head false、holds全false、两reader状态[0,0]，原host enable_rfid=1。原协议没有透传第二reader enable原始字段，不能声称独立复验该字段。没有额外送料或加热。

原生模型实机余量、持续打印期更新、日常自动插入接入、网页显示及同读头两卷/同时插入仍未验收。


## 后续 97c 原生预装验证

97b 在 ARM 前 DISCOVER 无回复而退出，没有运动；97c 在独立目录完成单槽受控预装。before count0/remaining0，after 样本插入count2、+12和 CMD13 field11均288873mm、SKU SME5DDA105，按1.75mm与PLA 1.24g/cm³估算861.6g，非称重结果。STOP后显式decoder −746mm三次一致（最后回退阶段），四槽ready、四head false；原配置/协议已恢复，临时extra和hold已清除。完整证据见 `../diagnostic95/native-guard/run-20261107-97c/`。日常自动入口、网页与同读头并发仍未完成。
