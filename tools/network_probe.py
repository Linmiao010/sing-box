#!/usr/bin/env python3
"""Collect curl samples without storing URLs, proxy credentials or response bodies.

Run one manually selected configuration at a time, in A/B/A/B order. Each batch
starts --concurrency independent curl processes; --count is the number of batches.
CSV rows repeat batch totals: count each (run_id, batch) once when aggregating.
No client policy, DNS, cache, node, service or kernel configuration is changed.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid


METRICS = (
    "time_namelookup", "time_connect", "time_appconnect", "time_starttransfer",
    "time_total", "speed_download", "size_download", "http_code",
    "num_redirects", "time_redirect",
)
COLUMNS = (
    "run_id", "started_utc", "label", "path", "family", "batch", "slot",
    "concurrency", "curl_exit_code", "success", "probe_error", *METRICS,
    "request_wall_seconds", "batch_wall_seconds", "batch_size_download",
    "batch_speed_download",
)


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default errors can echo a mistyped URL or credential.
        self.exit(2, "参数无效；使用 --help 查看用法。\n")


def arguments(argv):
    parser = PrivateArgumentParser(
        description="固定目标的 curl 采集；输出追加到 CSV，stdout 为本次 JSON 汇总。",
        epilog=(
            "标签由使用者脱敏，不要放 URL/令牌。时间字段多为累计值，不可相加。"
            "system 路径禁用 curl 环境代理，但仍经过系统路由/TUN；不会读取浏览器系统代理。"
            "显式代理的连接计时可能仅到本地代理，socks5h 不计完整目标 DNS。"
            "每次请求使用新 curl 进程；系统 DNS/服务端缓存由使用者另行控制并标注。"
            "吞吐需相同固定内容，建议 --expected-bytes 排除小响应；curl 无法代替浏览器 HAR。"
        ),
    )
    parser.add_argument("--label", required=True, help="人工脱敏标签，例如 A1-cold-fixed-node")
    parser.add_argument("--url", required=True, help="HTTP/HTTPS 目标；不会写入采集结果")
    parser.add_argument("--output", required=True, type=Path, help="追加 CSV 文件；不同进程请勿同时写入")
    parser.add_argument("--count", type=int, default=10, help="批次数，默认 10")
    parser.add_argument("--concurrency", type=int, default=1, help="每批请求并发数，默认 1")
    route = parser.add_mutually_exclusive_group()
    route.add_argument("--system-path", action="store_true", help="系统路由/TUN，禁用环境代理（默认）")
    route.add_argument("--proxy", help="已核实端口的 HTTP/SOCKS 代理，例如 http://127.0.0.1:7890")
    parser.add_argument("--family", choices=("auto", "4", "6"), default="auto", help="curl 连接地址族")
    parser.add_argument("--connect-timeout", type=float, default=10, help="连接预算，秒")
    parser.add_argument("--timeout", type=float, default=60, help="每次 curl 总预算，秒（含重定向）")
    parser.add_argument("--expected-bytes", type=int, help="可选：成功响应必须匹配的下载字节数")
    args = parser.parse_args(argv)
    try:
        target = urlsplit(args.url)
        valid_url = target.scheme in ("http", "https") and bool(target.hostname)
    except ValueError:
        valid_url = False
    if (not valid_url or not args.label.strip() or args.count < 1 or args.concurrency < 1
            or not all(math.isfinite(n) and n > 0 for n in (args.timeout, args.connect_timeout))
            or (args.expected_bytes is not None and args.expected_bytes < 0)):
        parser.error("invalid values")
    return args


def sample(command, args):
    start = time.perf_counter()
    row = {key: "" for key in METRICS}
    row.update(curl_exit_code="", success=0, probe_error="")
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, encoding="ascii", errors="replace", timeout=args.timeout + 5)
        row["curl_exit_code"] = result.returncode
        values = result.stdout.strip().split("\t")
        try:
            if len(values) != len(METRICS):
                raise ValueError("incomplete metrics")
            parsed = [float(value) for value in values]
            if not all(math.isfinite(value) and value >= 0 for value in parsed):
                raise ValueError("invalid metrics")
            row.update(zip(METRICS, parsed))
            for field in ("http_code", "num_redirects"):
                row[field] = int(row[field])
            row["success"] = int(
                result.returncode == 0 and 200 <= row["http_code"] < 300
                and (args.expected_bytes is None or row["size_download"] == args.expected_bytes)
            )
        except ValueError:
            row["probe_error"] = "invalid_metrics"
    except subprocess.TimeoutExpired:
        row["probe_error"] = "process_timeout"
    row["request_wall_seconds"] = time.perf_counter() - start
    return row


def statistics_for(values):
    values = sorted(values)
    result = {"n": len(values)}
    if values:
        result.update(p50=statistics.median(values), min=values[0], max=values[-1])
        if len(values) >= 30:
            result["p95"] = values[math.ceil(0.95 * len(values)) - 1]
    return result


def collect(args):
    curl = shutil.which("curl")
    if not curl:
        raise OSError("curl unavailable")
    command = [curl, "--disable", "--silent", "--location", "--max-redirs", "10",
               "--globoff", "--proto", "=http,https", "--proto-redir", "=http,https",
               "--connect-timeout", str(args.connect_timeout), "--max-time", str(args.timeout),
               "--output", os.devnull, "--write-out",
               "\t".join("%{" + field + "}" for field in METRICS)]
    if args.proxy:
        command += ["--proxy", args.proxy, "--noproxy", ""]
    else:
        command += ["--proxy", "", "--noproxy", "*"]
    if args.family != "auto":
        command += ["-" + args.family]
    command += ["--url", args.url]
    run_id = uuid.uuid4().hex
    metadata = dict(run_id=run_id, started_utc=datetime.now(timezone.utc).isoformat(),
                    label=args.label, path="proxy" if args.proxy else "system",
                    family=args.family, concurrency=args.concurrency)
    rows, good_batches = [], []
    started = time.perf_counter()
    with args.output.open("a+", encoding="utf-8", newline="") as stream:
        stream.seek(0)
        header = next(csv.reader(stream), None)
        if header is not None and header != list(COLUMNS):
            raise ValueError("CSV schema differs")
        stream.seek(0, os.SEEK_END)
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        if header is None:
            writer.writeheader()
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            for batch in range(1, args.count + 1):
                batch_start = time.perf_counter()
                futures = [pool.submit(sample, command, args) for _ in range(args.concurrency)]
                current = [future.result() for future in futures]
                walltime = time.perf_counter() - batch_start
                size = sum(row["size_download"] or 0 for row in current)
                # The denominator includes process launch and the slowest concurrent request.
                speed = size / walltime
                if all(row["success"] for row in current) and size > 0:
                    good_batches.append(speed)
                for slot, row in enumerate(current, 1):
                    row.update(metadata, batch=batch, slot=slot, batch_wall_seconds=walltime,
                               batch_size_download=size, batch_speed_download=speed)
                writer.writerows(current)
                stream.flush()
                rows.extend(current)
    successful = [row for row in rows if row["success"]]
    summary = dict(metadata, batches=args.count, requests=len(rows),
                   failed_requests=len(rows) - len(successful),
                   total_size_download=sum(row["size_download"] or 0 for row in rows),
                   wall_seconds=time.perf_counter() - started,
                   time_total_seconds=statistics_for([row["time_total"] for row in successful]),
                   time_starttransfer_seconds=statistics_for([row["time_starttransfer"] for row in successful]),
                   batch_speed_download_Bps=statistics_for(good_batches))
    print(json.dumps(summary, ensure_ascii=True))
    return int(bool(summary["failed_requests"]))


def main(argv=None):
    args = arguments(argv)
    try:
        return collect(args)
    except (OSError, ValueError):
        # Do not echo subprocess arguments, raw curl errors, or target URLs.
        print("采集无法完成：请检查 curl、输出目录权限及已有 CSV 表头。", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("采集已中断；已完成批次保留在 CSV。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
