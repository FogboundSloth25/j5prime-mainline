// SPDX-License-Identifier: GPL-2.0-only
/* Small, read-only-hardware diagnostic userspace for mainline stage0. */

/* Upstream Linux nolibc provides startup/syscalls; no glibc or BusyBox. */
#include <nolibc.h>

static void mount_virtual(const char *type, const char *target,
			  unsigned long flags)
{
	if (mount(type, target, type, flags, NULL) && errno != EBUSY)
		fprintf(stderr, "stage0: mount %s failed (errno %d)\n", target, errno);
}

static void print_file(const char *label, const char *path)
{
	char buffer[256];
	int fd = open(path, O_RDONLY | O_CLOEXEC);
	ssize_t size;
	unsigned int i;

	if (fd < 0) {
		printf("%s: unavailable (errno %d)\n", label, errno);
		return;
	}
	size = read(fd, buffer, sizeof(buffer) - 1);
	close(fd);
	if (size < 0) {
		printf("%s: read failed\n", label);
		return;
	}
	buffer[size] = '\0';
	/* Device-tree strings end in NUL; text sysfs files end in a newline. */
	for (i = 0; buffer[i]; i++)
		if (buffer[i] == '\n') {
			buffer[i] = '\0';
			break;
		}
	printf("%s: %s\n", label, buffer);
}

int main(void)
{
	const char marker[] = "<6>MAINLINE_STAGE0_USERSPACE_OK\n";
	struct utsname uts;
	unsigned int heartbeat = 0;
	int fd;

	/* Prevent accidental host execution from mounting host filesystems. */
	if (getpid() != 1) {
		fputs("stage0 /init must only run as PID 1; refusing host execution\n",
		      stderr);
		return 1;
	}

	mount_virtual("proc", "/proc", MS_NOSUID | MS_NODEV | MS_NOEXEC);
	mount_virtual("sysfs", "/sys", MS_NOSUID | MS_NODEV | MS_NOEXEC);
	mount_virtual("devtmpfs", "/dev", MS_NOSUID | MS_NOEXEC);

	fd = open("/dev/console", O_WRONLY | O_CLOEXEC);
	if (fd >= 0) {
		dup2(fd, STDOUT_FILENO);
		dup2(fd, STDERR_FILENO);
		if (fd != STDOUT_FILENO && fd != STDERR_FILENO)
			close(fd);
	}
	/* nolibc's stdio writes directly; there is no buffered stream state. */

	fd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
	if (fd >= 0) {
		if (write(fd, marker, sizeof(marker) - 1) < 0)
			perror("stage0: write kmsg");
		close(fd);
	}

	puts("\n----------------------------------------");
	puts("MAINLINE_STAGE0_USERSPACE_OK");
	puts("J5 Prime SM-G570F / Exynos7570 / rev04");
	if (!uname(&uts))
		printf("Kernel: %s %s (%s)\n", uts.sysname, uts.release, uts.machine);
	print_file("Board", "/proc/device-tree/model");
	print_file("Online CPUs", "/sys/devices/system/cpu/online");
	print_file("Clocksource", "/sys/devices/system/clocksource/clocksource0/current_clocksource");
	puts("No storage, USB/ADB, charging or thermal support.");
	puts("Keep this diagnostic short; return to known-working TWRP.");
	puts("----------------------------------------");

	/* Sleep/wakeup plus an advancing monotonic clock exercise both timers. */
	for (;;) {
		struct timespec now;

		sleep(5);
		if (!clock_gettime(CLOCK_MONOTONIC, &now))
			printf("stage0 heartbeat %u, uptime %lld s\n", ++heartbeat,
			       (long long)now.tv_sec);
		else
			perror("stage0: monotonic clock");
	}
}
