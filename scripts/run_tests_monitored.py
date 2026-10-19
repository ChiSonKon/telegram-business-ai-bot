# -*- coding: utf-8 -*-
"""安全测试运行器：带时间与内存上限监控。
防止 pytest 因无限循环或协程泄漏耗尽内存、占满磁盘。
"""
import sys
import time
import subprocess
import psutil

MAX_SECONDS = 400
MAX_MEMORY_MB = 1500

def get_tree_memory_mb(proc: psutil.Process) -> float:
    try:
        mem = proc.memory_info().rss
        for child in proc.children(recursive=True):
            try:
                mem += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return mem / (1024 * 1024)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0

def kill_tree(proc: psutil.Process):
    try:
        children = proc.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        proc.kill()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

def main():
    pytest_args = sys.argv[1:]
    if not pytest_args:
        pytest_args = ["tests_core", "-q", "-p", "no:cacheprovider"]

    cmd = [sys.executable, "-m", "pytest"] + pytest_args
    print(f"[Monitored Runner] 启动测试: {' '.join(cmd)}")
    print(f"[Monitored Runner] 监控上限: 时间 <= {MAX_SECONDS}s, 内存 <= {MAX_MEMORY_MB}MB")

    start_time = time.time()
    subp = subprocess.Popen(cmd)
    proc = psutil.Process(subp.pid)

    peak_mem_mb = 0.0
    killed_reason = None

    try:
        while subp.poll() is None:
            elapsed = time.time() - start_time
            if elapsed > MAX_SECONDS:
                killed_reason = f"超时 (运行时间 {elapsed:.1f}s > {MAX_SECONDS}s)"
                break

            current_mem = get_tree_memory_mb(proc)
            if current_mem > peak_mem_mb:
                peak_mem_mb = current_mem

            if current_mem > MAX_MEMORY_MB:
                killed_reason = f"内存超限 (当前内存 {current_mem:.1f}MB > {MAX_MEMORY_MB}MB)"
                break

            time.sleep(0.5)

        if killed_reason:
            print(f"\n[Monitored Runner 警报] 进程被强制终止: {killed_reason}!")
            kill_tree(proc)
            subp.wait()
            sys.exit(1)

        retcode = subp.wait()
        total_time = time.time() - start_time
        print(f"\n[Monitored Runner] 测试结束: 耗时 {total_time:.2f}s, 峰值内存 {peak_mem_mb:.1f}MB, 退出码 {retcode}")
        sys.exit(retcode)

    except KeyboardInterrupt:
        print("\n[Monitored Runner] 用户中断，清理进程树...")
        kill_tree(proc)
        subp.wait()
        sys.exit(130)

if __name__ == "__main__":
    main()
