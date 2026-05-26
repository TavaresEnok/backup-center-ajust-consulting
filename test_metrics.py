import psutil, time

# CPU - the key issue: first call always returns 0.0, needs baseline
psutil.cpu_percent(interval=None)  # initialize baseline
time.sleep(1)
c1 = psutil.cpu_percent(interval=None)
print(f'CPU (non-blocking after 1s init): {c1}%')
c2 = psutil.cpu_percent(interval=1)
print(f'CPU (blocking 1s): {c2}%')

# cpu_times_percent - equivalent to top
ct = psutil.cpu_times_percent(interval=1)
print(f'cpu_times: user={ct.user:.1f}% sys={ct.system:.1f}% idle={ct.idle:.1f}% => user+sys={ct.user+ct.system:.1f}%')

# Disk IO
io1 = psutil.disk_io_counters()
print(f'\nio1 read_bytes={io1.read_bytes} busy_time={io1.busy_time}')
time.sleep(3)
io2 = psutil.disk_io_counters()
rdiff = io2.read_bytes - io1.read_bytes
wdiff = io2.write_bytes - io1.write_bytes
bdiff = io2.busy_time - io1.busy_time
print(f'After 3s: read={rdiff/1024:.1f}KB={rdiff/1024/1024/3:.3f}MB/s write={wdiff/1024:.1f}KB={wdiff/1024/1024/3:.3f}MB/s')
print(f'busy_diff={bdiff}ms busy_pct={bdiff/3000*100:.2f}%')

# Check if busy_time is a cumulative counter from boot or per-interval
print(f'\nbusy_time is cumulative (large number): {io1.busy_time}ms = {io1.busy_time/1000/60:.1f}min')
