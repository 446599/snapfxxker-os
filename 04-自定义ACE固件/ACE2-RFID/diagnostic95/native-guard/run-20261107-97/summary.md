# 97O 原生预装验证等待记录

97O 已 OTA 并启动；该次测试建立本机 guard 后等待300秒，未观察到拔出、插入或主动送料。等待超时后执行 STOP，guard confirmed；本次没有取得新原生预装样本，不能算97几何模型的实机验证。

退出后两读头恢复工作、临时端口hold全部释放，临时guard和配置include已卸载；原 printer.cfg 和协议文件哈希恢复。fresh 同UID/97O、四槽ready、四head false、两reader state [0,0]、原host可见 enable_rfid=1。详细时间序列和结果保存在此目录。
