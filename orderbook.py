import re
import json
import pandas as pd
from datetime import datetime
import os
import glob

def parse_timestamp(timestamp_str):
    """
    将timestamp字符串转换为datetime格式
    """
    try:
        # 解析ISO格式时间戳 (2025-10-28T09:23:32.473)
        return pd.to_datetime(timestamp_str)
    except:
        return None

def convert_backend_timestamp(backend_timestamp):
    """
    将后端时间戳（毫秒）转换为datetime格式
    """
    try:
        # 后端时间戳是毫秒级别的，需要转换为秒
        timestamp_sec = int(backend_timestamp) / 1000
        return pd.to_datetime(timestamp_sec, unit='s')
    except:
        return None

def process_message(message, receive_time, records):
    """
    处理单条消息并提取相关信息
    """
    event_type = message.get('event_type', '')
    market = message.get('market', '')
    backend_timestamp = message.get('timestamp', '')
    backend_time = convert_backend_timestamp(backend_timestamp) if backend_timestamp else None
    
    # 处理订单簿数据 (book event)
    if event_type == 'book':
        asset_id = message.get('asset_id', '')
        last_trade_price = message.get('last_trade_price', '')
        hash_val = message.get('hash', '')
        
        # 处理买单 (bids)
        if 'bids' in message:
            for bid in message['bids']:
                records.append({
                    'receive_time': receive_time,
                    'backend_time': backend_time,
                    'backend_timestamp': backend_timestamp,
                    'event_type': event_type,
                    'market': market,
                    'asset_id': asset_id,
                    'side': 'BUY',
                    'price': bid.get('price', ''),
                    'size': bid.get('size', ''),
                    'hash': hash_val,
                    'last_trade_price': last_trade_price,
                    'best_bid': '',
                    'best_ask': ''
                })
        
        # 处理卖单 (asks)
        if 'asks' in message:
            for ask in message['asks']:
                records.append({
                    'receive_time': receive_time,
                    'backend_time': backend_time,
                    'backend_timestamp': backend_timestamp,
                    'event_type': event_type,
                    'market': market,
                    'asset_id': asset_id,
                    'side': 'SELL',
                    'price': ask.get('price', ''),
                    'size': ask.get('size', ''),
                    'hash': hash_val,
                    'last_trade_price': last_trade_price,
                    'best_bid': '',
                    'best_ask': ''
                })
    
    # 处理价格变化数据 (price_change event)
    elif event_type == 'price_change':
        if 'price_changes' in message:
            for change in message['price_changes']:
                records.append({
                    'receive_time': receive_time,
                    'backend_time': backend_time,
                    'backend_timestamp': backend_timestamp,
                    'event_type': event_type,
                    'market': market,
                    'asset_id': change.get('asset_id', ''),
                    'side': change.get('side', ''),
                    'price': change.get('price', ''),
                    'size': change.get('size', ''),
                    'hash': change.get('hash', ''),
                    'last_trade_price': '',
                    'best_bid': change.get('best_bid', ''),
                    'best_ask': change.get('best_ask', '')
                })

def parse_single_log_file(log_file_path):
    """
    解析单个日志文件
    
    参数:
        log_file_path: 单个日志文件路径
    
    返回:
        records列表
    """
    records = []
    
    print(f"正在处理文件: {log_file_path}")
    
    with open(log_file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        # 匹配包含 "Received message:" 的行
        if 'Received message:' in line:
            # 提取接收时间（日志行最前面的时间）
            time_match = re.match(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)', line)
            if time_match:
                receive_time = parse_timestamp(time_match.group(1))
            else:
                receive_time = None
            
            # 提取JSON数据部分
            json_start = line.find('Received message:') + len('Received message:')
            json_str = line[json_start:].strip()
            
            # 跳过PING/PONG消息
            if json_str in ['PONG', 'PING']:
                i += 1
                continue
            
            try:
                # 解析JSON数据
                data = json.loads(json_str)
                
                # 处理列表格式的数据
                if isinstance(data, list):
                    for item in data:
                        process_message(item, receive_time, records)
                else:
                    process_message(data, receive_time, records)
                    
            except json.JSONDecodeError as e:
                print(f"JSON解析错误 at line {i+1} in {log_file_path}: {e}")
        
        i += 1
    
    print(f"文件 {log_file_path} 解析完成，提取了 {len(records)} 条记录")
    return records

def parse_multiple_orderbook_logs(log_pattern, csv_file_path, sort_by_time=True):
    """
    解析多个orderbook日志文件并合并为一个CSV
    
    参数:
        log_pattern: 日志文件匹配模式，例如 "orderbook*.log" 或 ["file1.log", "file2.log"]
        csv_file_path: 输出CSV文件路径
        sort_by_time: 是否按时间排序合并后的数据
    
    返回:
        合并后的DataFrame
    """
    
    # 获取所有匹配的日志文件
    if isinstance(log_pattern, list):
        log_files = log_pattern
    else:
        log_files = glob.glob(log_pattern)
    
    if not log_files:
        print(f"未找到匹配的日志文件: {log_pattern}")
        return None
    
    # 按文件名排序
    log_files.sort()
    
    print(f"找到 {len(log_files)} 个日志文件:")
    for f in log_files:
        print(f"  - {f}")
    print()
    
    # 合并所有记录
    all_records = []
    
    for log_file in log_files:
        try:
            records = parse_single_log_file(log_file)
            all_records.extend(records)
        except Exception as e:
            print(f"处理文件 {log_file} 时出错: {e}")
            continue
    
    if not all_records:
        print("没有提取到任何记录")
        return None
    
    # 转换为DataFrame
    df = pd.DataFrame(all_records)
    
    # 按时间排序
    if sort_by_time and 'receive_time' in df.columns:
        df = df.sort_values('receive_time')
        print(f"\n数据已按接收时间排序")
    
    # 设置接收时间为索引
    if 'receive_time' in df.columns:
        df.set_index('receive_time', inplace=True)
    
    # 保存为CSV
    df.to_csv(csv_file_path, encoding='utf-8-sig')
    
    print(f"\n成功解析所有日志文件")
    print(f"输出文件: {csv_file_path}")
    print(f"总共 {len(df)} 条记录")
    print(f"时间范围: {df.index.min()} 到 {df.index.max()}")
    
    return df

def parse_orderbook_logs_to_separate_csvs(log_pattern, output_dir="output"):
    """
    解析多个日志文件，每个文件生成一个单独的CSV
    
    参数:
        log_pattern: 日志文件匹配模式
        output_dir: 输出目录
    """
    
    # 创建输出目录
    os.makedirs(output_dir, exist_ok=True)
    
    # 获取所有匹配的日志文件
    if isinstance(log_pattern, list):
        log_files = log_pattern
    else:
        log_files = glob.glob(log_pattern)
    
    if not log_files:
        print(f"未找到匹配的日志文件: {log_pattern}")
        return
    
    log_files.sort()
    
    print(f"找到 {len(log_files)} 个日志文件\n")
    
    for log_file in log_files:
        try:
            # 生成输出CSV文件名
            base_name = os.path.splitext(os.path.basename(log_file))[0]
            csv_file = os.path.join(output_dir, f"{base_name}.csv")
            
            # 解析单个文件
            records = parse_single_log_file(log_file)
            
            if records:
                df = pd.DataFrame(records)
                if 'receive_time' in df.columns:
                    df = df.sort_values('receive_time')
                    df.set_index('receive_time', inplace=True)
                
                df.to_csv(csv_file, encoding='utf-8-sig')
                print(f"已生成: {csv_file} ({len(df)} 条记录)\n")
            
        except Exception as e:
            print(f"处理文件 {log_file} 时出错: {e}\n")

# 使用示例
if __name__ == "__main__":
    
    # ========== 方法1: 合并所有日志文件为一个CSV ==========
    print("=" * 60)
    print("方法1: 合并所有日志文件")
    print("=" * 60)
    
    # 使用通配符匹配所有日志文件
    df_merged = parse_multiple_orderbook_logs(
        log_pattern="websocket_20251029_092015.log*",  # 匹配 orderbook 开头的所有 .log 文件
        csv_file_path="orderbook_merged.csv",
        sort_by_time=True
    )
    
    if df_merged is not None:
        print("\n数据统计:")
        print(f"事件类型分布:\n{df_merged['event_type'].value_counts()}")
        print(f"\n交易方向分布:\n{df_merged['side'].value_counts()}")
        print("\n前5行数据预览:")
        print(df_merged.head())
    
    print("\n" + "=" * 60 + "\n")
    
#     # ========== 方法2: 每个日志文件生成单独的CSV ==========
    # print("=" * 60)
    # print("方法2: 每个日志文件生成单独的CSV")
    # print("=" * 60)
    
    # parse_orderbook_logs_to_separate_csvs(
        # log_pattern="orderbook*.log",
        # output_dir="orderbook_csvs"
    # )
    
    # print("\n" + "=" * 60 + "\n")
    
    # # ========== 方法3: 手动指定文件列表 ==========
    # print("=" * 60)
    # print("方法3: 手动指定文件列表")
    # print("=" * 60)
    
    # file_list = [
        # "orderbook.log",
        # "orderbook.log.1",
        # "orderbook.log.2"
    # ]
    
    # df_manual = parse_multiple_orderbook_logs(
        # log_pattern=file_list,
        # csv_file_path="orderbook_manual_merged.csv",
        # sort_by_time=True
    # )