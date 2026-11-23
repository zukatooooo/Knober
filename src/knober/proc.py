"""Find running knober daemons via /proc (no pid file to go stale)."""
import os
import time


def find_daemons():
    """PIDs of running daemons, oldest first. A daemon is any process started
    as `python ... -m knober.daemon` (that's how the daemon always re-launches itself)."""
    me = os.getpid()
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == me:
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        for i, a in enumerate(args[:-1]):
            if a == b"-m" and args[i + 1] == b"knober.daemon":
                pids.append(int(entry))
                break
    return sorted(pids, key=start_time)


def start_time(pid):
    """Process start as a Unix timestamp (0 if the process is gone)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            # field 22 (starttime, in clock ticks since boot); split after the ")" of comm
            ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/stat") as f:
            btime = next(int(l.split()[1]) for l in f if l.startswith("btime "))
    except (OSError, ValueError, IndexError, StopIteration):
        return 0
    return btime + ticks / os.sysconf("SC_CLK_TCK")


def alive(pid):
    return os.path.exists(f"/proc/{pid}")


def format_age(seconds):
    """3725 -> '1h 2min'. Shows the two largest non-zero units."""
    d, rest = divmod(int(seconds), 86400)
    h, rest = divmod(rest, 3600)
    m, s = divmod(rest, 60)
    parts = [f"{v}{u}" for v, u in ((d, "d"), (h, "h"), (m, "min"), (s, "s")) if v]
    return " ".join(parts[:2]) or "0s"


def describe(pid):
    t = start_time(pid)
    return f"pid {pid}, since {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t))} ({format_age(time.time() - t)} ago)"
