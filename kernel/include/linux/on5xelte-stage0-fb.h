/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_ON5XELTE_STAGE0_FB_H
#define _LINUX_ON5XELTE_STAGE0_FB_H
#include <linux/init.h>
#include <linux/types.h>
#include <linux/platform_data/simplefb.h>

/* Visited code paths only: BEGIN/RETURN do not mean working IRQs/timers. */
enum on5xelte_stage0_progress {
	ON5XELTE_STAGE0_MEMORY_OK = 1,
	ON5XELTE_STAGE0_PAGING_OK,
	ON5XELTE_STAGE0_IRQ_BEGIN,
	ON5XELTE_STAGE0_TIMER_BEGIN,
	ON5XELTE_STAGE0_TIMER_RETURN,
	ON5XELTE_STAGE0_CONSOLE_RETURN,
	ON5XELTE_STAGE0_MCT_CLOCK_FAILED,
};

#ifdef CONFIG_FB_ON5XELTE_STAGE0
void __init on5xelte_stage0_fb_prepare(phys_addr_t fdt_phys);
void __init on5xelte_stage0_fb_checkpoint(enum on5xelte_stage0_progress phase);
/* Internal renderer: the caller must have reserved/validated full backing. */
int __init on5xelte_stage0_progress_draw(phys_addr_t physical, u64 protected_bytes,
		const struct simplefb_platform_data *data, enum on5xelte_stage0_progress phase);
#else
static inline void on5xelte_stage0_fb_prepare(phys_addr_t fdt_phys) { }
static inline void on5xelte_stage0_fb_checkpoint(enum on5xelte_stage0_progress phase) { }
#endif
#endif
