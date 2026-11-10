# SPDX-License-Identifier: GPL-3.0-or-later
# The distributed binary uses Debian libnl 3.7.0-2, not lib/libnl (3.9.0).
libnl-build:
	@mkdir -p $(BUILD_DIR)/lib $(LIBNL_PREFIX)/lib $(LIBNL_PREFIX)/include/libnl3
	@cp $(U1_CROSS_SYSROOT)/usr/lib/aarch64-linux-gnu/libnl-3.a $(U1_CROSS_SYSROOT)/usr/lib/aarch64-linux-gnu/libnl-genl-3.a $(BUILD_DIR)/lib/
	@cp $(U1_CROSS_SYSROOT)/usr/lib/aarch64-linux-gnu/libnl-3.a $(U1_CROSS_SYSROOT)/usr/lib/aarch64-linux-gnu/libnl-genl-3.a $(LIBNL_PREFIX)/lib/
	@cp -R $(U1_CROSS_SYSROOT)/usr/include/libnl3/netlink $(LIBNL_PREFIX)/include/libnl3/
