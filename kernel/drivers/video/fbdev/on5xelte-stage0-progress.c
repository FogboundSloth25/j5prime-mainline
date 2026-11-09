// SPDX-License-Identifier: GPL-2.0-only
/*
 * Bounded early framebuffer-memory marks, not a display register driver.
 * The separate gate validates/reserves the complete live scanout backing.
 * No allocation, timer, console, DT mutation or display MMIO write here.
 */
#include <linux/errno.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/on5xelte-stage0-fb.h>
#include <linux/sizes.h>
#include <linux/string.h>
#include <linux/types.h>
#include <asm/barrier.h>
#include <asm/byteorder.h>

#define PROGRESS_CELL 16U
#define PROGRESS_WIDTH 720U
#define PROGRESS_HEIGHT 1280U
#define PROGRESS_RAM_END 0x60000000ULL
#define PROGRESS_FIRMWARE_HIGH 0x710ef000ULL

static const u8 progress_digits[8][7] __initconst = {
	{ 0 },
	{ 4, 12, 4, 4, 4, 4, 14 },
	{ 14, 17, 1, 2, 4, 8, 31 },
	{ 30, 1, 1, 14, 1, 1, 30 },
	{ 2, 6, 10, 18, 31, 2, 2 },
	{ 31, 16, 16, 30, 1, 1, 30 },
	{ 14, 16, 16, 30, 17, 17, 14 },
	{ 31, 1, 2, 4, 8, 8, 8 },
};

static int __init progress_format(const char *format, u32 *bpp, bool *swap)
{
	if (!format)
		return -EINVAL;
	*bpp = 4;
	*swap = false;
	if (!strcmp(format, "a8r8g8b8") || !strcmp(format, "x8r8g8b8"))
		return 0;
	if (!strcmp(format, "a8b8g8r8") || !strcmp(format, "x8b8g8r8")) {
		*swap = true;
		return 0;
	}
	if (!strcmp(format, "r5g6b5")) {
		*bpp = 2;
		return 0;
	}
	return -EINVAL;
}

static void __init progress_pixel(u8 *pixel, u32 rgb, u32 bpp, bool swap)
{
	__le32 wide;
	__le16 narrow;
	u32 value;

	if (bpp == 2) {
		value = ((rgb >> 19) & 31) << 11 |
				((rgb >> 10) & 63) << 5 | ((rgb >> 3) & 31);
		narrow = cpu_to_le16(value);
		memcpy(pixel, &narrow, sizeof(narrow));
		return;
	}
	value = swap ? ((rgb & 255) << 16) | (rgb & 0xff00) | (rgb >> 16) : rgb;
	wide = cpu_to_le32(0xff000000U | value);
	memcpy(pixel, &wide, sizeof(wide));
}

int __init on5xelte_stage0_progress_draw(phys_addr_t physical, u64 protected_bytes,
		const struct simplefb_platform_data *data, enum on5xelte_stage0_progress phase)
{
	u8 row[PROGRESS_CELL * 4];
	void __iomem *base;
	u64 mapped_bytes, column;
	u32 bpp, x, y, background;
	bool swap, glyph, border;

	/* These are redundant range checks, not a replacement for the gate. */
	if (phase < ON5XELTE_STAGE0_MEMORY_OK || phase > ON5XELTE_STAGE0_MCT_CLOCK_FAILED ||
		!data || data->width != PROGRESS_WIDTH || data->height != PROGRESS_HEIGHT ||
		progress_format(data->format, &bpp, &swap) || data->stride != PROGRESS_WIDTH * bpp ||
		!protected_bytes || physical % SZ_4K || physical < PROGRESS_RAM_END ||
		physical >= PROGRESS_FIRMWARE_HIGH || protected_bytes > PROGRESS_FIRMWARE_HIGH - physical ||
		(u64)data->stride * data->height > protected_bytes)
		return -EINVAL;
	column = (u64)(phase - 1) * PROGRESS_CELL * bpp;
	mapped_bytes = (PROGRESS_CELL - 1) * (u64)data->stride + column + PROGRESS_CELL * bpp;
	if (mapped_bytes > protected_bytes || column + PROGRESS_CELL * bpp > data->stride)
		return -EINVAL;
	base = early_ioremap(physical, mapped_bytes);
	if (!base)
		return -ENOMEM;
	background = phase == ON5XELTE_STAGE0_MCT_CLOCK_FAILED ? 0xe03030 : 0x20d060;
	for (y = 0; y < PROGRESS_CELL; y++) {
		for (x = 0; x < PROGRESS_CELL; x++) {
			border = !x || x == PROGRESS_CELL - 1 || !y || y == PROGRESS_CELL - 1;
			glyph = x >= 3 && x < 13 && y >= 1 && y < 15 &&
					(progress_digits[phase][(y - 1) / 2] & (1U << (4 - (x - 3) / 2)));
			progress_pixel(row + x * bpp, border || glyph ? 0xffffff : background, bpp, swap);
		}
		/* The only writes target one checked visible framebuffer cell. */
		memcpy_toio(base + (u64)y * data->stride + column, row, PROGRESS_CELL * bpp);
	}
	wmb();
	early_iounmap(base, mapped_bytes);
	return 0; /* Completed memory writes are not proof of visible panel output. */
}
