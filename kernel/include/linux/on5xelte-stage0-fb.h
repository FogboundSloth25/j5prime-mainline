/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_ON5XELTE_STAGE0_FB_H
#define _LINUX_ON5XELTE_STAGE0_FB_H
#include <linux/init.h>
#include <linux/types.h>
#ifdef CONFIG_FB_ON5XELTE_STAGE0
void __init on5xelte_stage0_fb_prepare(phys_addr_t fdt_phys);
#else
static inline void on5xelte_stage0_fb_prepare(phys_addr_t fdt_phys) { }
#endif
#endif
