# 同读头双槽受控验证：guard 差异设计（未实现、不可运行）

基于 run4 已验证 `diagnostic95/native-guard/ace_native_guard.py`，不改固件或启动自然进料：

```diff
- 固定内部槽 2，单槽会话和单槽 STOP
+ 固定 ACE0、共享 reader1、内部槽 2 与 3；各槽独立插入代次、seen_busy、decoder 和 quiet 窗口
+ 两槽 INSERT 边沿均由 97O 原生状态机触发；不在主机启动第二个运动，不实现互等队列
+ guard 拦截目标 pair 的旧 host C-flow；非目标插入仍 STOP/终止，其他 ACE 保持原逻辑
+ 任何 head 触发、传感器缺失、取消/断连或不确定：固定槽2/槽3逐一 checked STOP，保留两次各自ACK和静止证据
+ 每槽独立 fresh STATUS + 显式 decoder 持续稳定至少2秒后标记该槽停稳；自然phase的短ready、decoder重置或方向变化重置该槽窗口
+ 全局完成要求两个槽均无运行/已停稳；其他槽活动立即STOP相关槽且终止；不以其中一个槽ready推断另一个ready
+ 事件和结果先保留各槽原始时序、独立 epoch、CMD13 原始上下文和不确定原因；相同UID、碰撞或天线可同时看到两卷时不赋归属，显示 pending/ambiguous
```

本机每50ms先采四head，遥测用单在途/令牌，STOP前登记inflight防重入；故障保持可恢复锁存。全程受有限时间/行程约束，不通过手动CMD68或RF扫描抢先STOP。重启或断线更换transport使所有采样失效。两槽同时插入和替换时，主机不借用缓存确认卷；CMD13可读不等于可归属。

必须在部署前对两槽STOP分别失败、迟回、同一reader第二job排队、双head/异槽、传感器缺失、decoder重置/反向、取消、超时、单槽/双槽正常完成做生产路径测试。物理出料路径/用户现场准备由主代理协调，未ARM、未执行。
