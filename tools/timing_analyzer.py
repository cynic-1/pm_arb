#!/usr/bin/env python3
"""
延迟统计分析工具

用于分析套利程序的时间测量数据，生成统计报告和可视化图表
"""

import argparse
import sys
import json
from pathlib import Path
from typing import List, Dict, Any
from collections import defaultdict
import statistics

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from arbitrage_core.timing import get_timing_tracker, get_token_bucket_monitor


def analyze_timing_data():
    """分析时间测量数据并生成报告"""
    tracker = get_timing_tracker()
    tb_monitor = get_token_bucket_monitor()

    sessions = tracker.get_all_sessions()

    if not sessions:
        print("❌ 没有找到任何时间测量数据")
        return

    print(f"\n{'='*80}")
    print(f"⏱️  套利程序延迟分析报告")
    print(f"{'='*80}\n")

    # 基本统计
    total_sessions = len(sessions)
    successful_sessions = [s for s in sessions if s.success]
    failed_sessions = [s for s in sessions if not s.success]

    success_rate = len(successful_sessions) / total_sessions * 100 if total_sessions > 0 else 0

    print(f"📊 总体统计:")
    print(f"  - 总执行次数: {total_sessions}")
    print(f"  - 成功次数: {len(successful_sessions)} ({len(successful_sessions)/total_sessions*100:.1f}%)")
    print(f"  - 失败次数: {len(failed_sessions)} ({len(failed_sessions)/total_sessions*100:.1f}%)")
    print(f"  - 成功率: {success_rate:.2f}%\n")

    # 总耗时统计
    all_elapsed_times = [s.total_elapsed for s in sessions]
    success_elapsed_times = [s.total_elapsed for s in successful_sessions]
    failed_elapsed_times = [s.total_elapsed for s in failed_sessions]

    threshold_150ms = 150.0
    within_threshold = len([t for t in all_elapsed_times if t < threshold_150ms])
    within_threshold_rate = within_threshold / len(all_elapsed_times) * 100 if all_elapsed_times else 0

    print(f"⏱️  总耗时统计:")
    print(f"  全部执行:")
    print(f"    - 平均: {statistics.mean(all_elapsed_times):.2f}ms")
    print(f"    - 中位数: {statistics.median(all_elapsed_times):.2f}ms")
    print(f"    - 最小: {min(all_elapsed_times):.2f}ms")
    print(f"    - 最大: {max(all_elapsed_times):.2f}ms")
    print(f"    - 标准差: {statistics.stdev(all_elapsed_times):.2f}ms" if len(all_elapsed_times) > 1 else "    - 标准差: N/A")
    print(f"    - <150ms次数: {within_threshold}/{len(all_elapsed_times)} ({within_threshold_rate:.1f}%)")

    if success_elapsed_times:
        print(f"\n  成功执行:")
        print(f"    - 平均: {statistics.mean(success_elapsed_times):.2f}ms")
        print(f"    - 中位数: {statistics.median(success_elapsed_times):.2f}ms")
        print(f"    - 最小: {min(success_elapsed_times):.2f}ms")
        print(f"    - 最大: {max(success_elapsed_times):.2f}ms")

    if failed_elapsed_times:
        print(f"\n  失败执行:")
        print(f"    - 平均: {statistics.mean(failed_elapsed_times):.2f}ms")
        print(f"    - 中位数: {statistics.median(failed_elapsed_times):.2f}ms")
        print(f"    - 最小: {min(failed_elapsed_times):.2f}ms")
        print(f"    - 最大: {max(failed_elapsed_times):.2f}ms")

    # 各阶段耗时统计
    print(f"\n{'='*80}")
    print(f"📈 各阶段耗时详细分析:")
    print(f"{'='*80}\n")

    stage_times = defaultdict(list)
    for session in sessions:
        for point in session.points:
            stage_times[point.name].append(point.delta_from_previous)

    # 按名称排序并输出
    for stage_name in sorted(stage_times.keys()):
        times = stage_times[stage_name]
        if not times:
            continue

        print(f"  {stage_name}:")
        print(f"    - 次数: {len(times)}")
        print(f"    - 平均: {statistics.mean(times):.2f}ms")
        print(f"    - 中位数: {statistics.median(times):.2f}ms")
        print(f"    - 最小: {min(times):.2f}ms")
        print(f"    - 最大: {max(times):.2f}ms")
        if len(times) > 1:
            print(f"    - 标准差: {statistics.stdev(times):.2f}ms")
            # P95
            sorted_times = sorted(times)
            p95_index = int(len(sorted_times) * 0.95)
            print(f"    - P95: {sorted_times[p95_index]:.2f}ms")
        print()

    # Token Bucket统计
    print(f"{'='*80}")
    print(f"🪣 Token Bucket 统计:")
    print(f"{'='*80}\n")

    tb_stats = tb_monitor.get_statistics()
    if tb_stats['total_requests'] > 0:
        print(f"  - 总请求数: {tb_stats['total_requests']}")
        print(f"  - 阻塞次数: {tb_stats['blocked_count']}")
        print(f"  - 阻塞率: {tb_stats['blocked_rate']*100:.2f}%")

        if 'mean_wait_time' in tb_stats:
            print(f"  - 平均等待: {tb_stats['mean_wait_time']:.2f}ms")
            print(f"  - 中位数等待: {tb_stats['median_wait_time']:.2f}ms")
            print(f"  - 最大等待: {tb_stats['max_wait_time']:.2f}ms")
            print(f"  - P95等待: {tb_stats['p95_wait_time']:.2f}ms")
    else:
        print("  暂无Token Bucket统计数据")

    # 成功vs失败的对比分析
    if successful_sessions and failed_sessions:
        print(f"\n{'='*80}")
        print(f"🔍 成功 vs 失败 对比分析:")
        print(f"{'='*80}\n")

        # 对比各阶段耗时
        success_stage_times = defaultdict(list)
        failed_stage_times = defaultdict(list)

        for session in successful_sessions:
            for point in session.points:
                success_stage_times[point.name].append(point.delta_from_previous)

        for session in failed_sessions:
            for point in session.points:
                failed_stage_times[point.name].append(point.delta_from_previous)

        # 找出差异最大的阶段
        all_stages = set(success_stage_times.keys()) | set(failed_stage_times.keys())

        for stage_name in sorted(all_stages):
            if stage_name not in success_stage_times or stage_name not in failed_stage_times:
                continue

            success_avg = statistics.mean(success_stage_times[stage_name])
            failed_avg = statistics.mean(failed_stage_times[stage_name])
            diff = failed_avg - success_avg
            diff_pct = (diff / success_avg * 100) if success_avg > 0 else 0

            if abs(diff) > 5:  # 只显示差异大于5ms的阶段
                print(f"  {stage_name}:")
                print(f"    - 成功平均: {success_avg:.2f}ms")
                print(f"    - 失败平均: {failed_avg:.2f}ms")
                print(f"    - 差异: {diff:+.2f}ms ({diff_pct:+.1f}%)")
                if abs(diff_pct) > 50:
                    print(f"    ⚠️  该阶段可能是失败的主要原因!")
                print()

    # 瓶颈识别
    print(f"{'='*80}")
    print(f"🎯 性能瓶颈识别:")
    print(f"{'='*80}\n")

    # 找出耗时最多的3个阶段
    avg_stage_times = {
        name: statistics.mean(times)
        for name, times in stage_times.items()
    }
    sorted_stages = sorted(avg_stage_times.items(), key=lambda x: x[1], reverse=True)

    print("  耗时最长的3个阶段:")
    for i, (stage_name, avg_time) in enumerate(sorted_stages[:3], 1):
        pct_of_total = (avg_time / statistics.mean(all_elapsed_times) * 100) if all_elapsed_times else 0
        print(f"    {i}. {stage_name}: {avg_time:.2f}ms (占比 {pct_of_total:.1f}%)")

    print(f"\n{'='*80}")
    print(f"💡 优化建议:")
    print(f"{'='*80}\n")

    # 根据数据给出优化建议
    if within_threshold_rate < 50:
        print("  ⚠️  当前只有 {:.1f}% 的执行在150ms内完成，建议优先优化:".format(within_threshold_rate))
        for stage_name, avg_time in sorted_stages[:3]:
            if avg_time > 20:
                print(f"    - 优化 {stage_name} (当前平均 {avg_time:.2f}ms)")

    if tb_stats['total_requests'] > 0 and tb_stats['blocked_rate'] > 0.2:
        print(f"  ⚠️  Token Bucket阻塞率过高 ({tb_stats['blocked_rate']*100:.1f}%)")
        print(f"    建议: 提升 OPINION_MAX_RPS 配置")

    if 'mean_wait_time' in tb_stats and tb_stats['mean_wait_time'] > 100:
        print(f"  ⚠️  Token Bucket平均等待时间过长 ({tb_stats['mean_wait_time']:.2f}ms)")
        print(f"    建议: 增加令牌桶容量或提升补充速率")

    print(f"\n{'='*80}\n")


def export_json(output_file: str):
    """导出JSON格式的统计数据"""
    tracker = get_timing_tracker()
    tb_monitor = get_token_bucket_monitor()

    data = {
        "sessions": [],
        "statistics": tracker.get_statistics(),
        "token_bucket": tb_monitor.get_statistics()
    }

    for session in tracker.get_all_sessions():
        session_data = {
            "session_id": session.session_id,
            "start_time": session.start_time,
            "total_elapsed": session.total_elapsed,
            "success": session.success,
            "metadata": session.metadata,
            "points": [
                {
                    "name": p.name,
                    "timestamp": p.timestamp,
                    "elapsed_from_start": p.elapsed_from_start,
                    "delta_from_previous": p.delta_from_previous
                }
                for p in session.points
            ]
        }
        data["sessions"].append(session_data)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"✅ 数据已导出到: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description="分析套利程序的延迟数据",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--export-json",
        metavar="FILE",
        help="导出JSON格式的统计数据到指定文件"
    )

    args = parser.parse_args()

    if args.export_json:
        export_json(args.export_json)
    else:
        analyze_timing_data()


if __name__ == "__main__":
    main()
