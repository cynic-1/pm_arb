#!/usr/bin/env python3
"""
删除JSON文件中question字段包含"FDV"的对象，并删除op_rules和poly_rules字段
可选：按cutoff_at字段从小到大排序
"""
import json
import sys
from pathlib import Path


def remove_fdv_items(input_file, output_file=None, remove_rules=True, sort_by_cutoff=False):
    """
    从JSON文件中删除question包含"FDV"的对象，并删除op_rules和poly_rules字段

    Args:
        input_file: 输入的JSON文件路径
        output_file: 输出文件路径（可选，默认会覆盖原文件）
        remove_rules: 是否删除op_rules和poly_rules字段（默认True）
        sort_by_cutoff: 是否按cutoff_at字段从小到大排序（默认False）
    """
    # 读取JSON文件
    print(f"正在读取文件: {input_file}")
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 检查数据类型
    if not isinstance(data, list):
        print("错误: JSON文件必须包含一个数组")
        return

    original_count = len(data)
    print(f"原始对象数量: {original_count}")

    # 过滤掉question包含"FDV"的对象
    filtered_data = [
        item for item in data
        if not (isinstance(item.get('question'), str) and 'FDV' in item.get('question', ''))
    ]

    removed_count = original_count - len(filtered_data)
    print(f"删除了 {removed_count} 个包含'FDV'的对象")
    print(f"剩余对象数量: {len(filtered_data)}")

    # 删除op_rules和poly_rules字段
    if remove_rules:
        fields_removed_count = 0
        for item in filtered_data:
            if 'op_rules' in item:
                del item['op_rules']
                fields_removed_count += 1
            if 'poly_rules' in item:
                del item['poly_rules']
        print(f"从 {fields_removed_count} 个对象中删除了 op_rules 和 poly_rules 字段")

    # 按cutoff_at排序
    if sort_by_cutoff:
        filtered_data.sort(key=lambda x: x.get('cutoff_at', float('inf')))
        print("已按 cutoff_at 字段从小到大排序")

    # 确定输出文件
    if output_file is None:
        output_file = input_file

    # 写入结果
    print(f"正在写入文件: {output_file}")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(filtered_data, f, ensure_ascii=False, indent=2)

    print("完成!")

    # 显示被删除的对象的question
    if removed_count > 0:
        print("\n被删除的对象question列表:")
        removed_items = [
            item for item in data
            if isinstance(item.get('question'), str) and 'FDV' in item.get('question', '')
        ]
        for i, item in enumerate(removed_items, 1):
            print(f"  {i}. {item.get('question')}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python remove_fdv.py <input_file> [output_file] [--keep-rules] [--sort-by-cutoff]")
        print("示例: python remove_fdv.py 1228.json")
        print("示例: python remove_fdv.py 1228.json 1228_filtered.json")
        print("示例: python remove_fdv.py 1228.json --keep-rules  # 保留rules字段")
        print("示例: python remove_fdv.py 1228.json --sort-by-cutoff  # 按cutoff_at排序")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = None
    remove_rules = True
    sort_by_cutoff = True

    # 解析参数
    for i in range(2, len(sys.argv)):
        arg = sys.argv[i]
        if arg == '--keep-rules':
            remove_rules = False
        elif arg == '--sort-by-cutoff':
            sort_by_cutoff = True
        elif output_file is None and not arg.startswith('--'):
            output_file = arg

    if not Path(input_file).exists():
        print(f"错误: 文件不存在 - {input_file}")
        sys.exit(1)

    remove_fdv_items(input_file, output_file, remove_rules, sort_by_cutoff)
