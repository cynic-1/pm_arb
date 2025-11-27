import json

# 读取 unmatched_markets.json
with open('unmatched_markets.json', 'r', encoding='utf-8') as f:
    unmatched = json.load(f)

# 读取现有的 market_matches.json
try:
    with open('market_matches.json', 'r', encoding='utf-8') as f:
        existing_matches = json.load(f)
except FileNotFoundError:
    existing_matches = []

# 转换格式
new_matches = []

for parent in unmatched:
    parent_title = parent['parent_title']
    opinion_children = parent['opinion_children']
    polymarket_children = parent['polymarket_children']
    
    # 确保两边的子市场数量一致
    if len(opinion_children) != len(polymarket_children):
        print(f"⚠️ 警告: '{parent_title}' 的子市场数量不匹配")
        print(f"   Opinion: {len(opinion_children)}, Polymarket: {len(polymarket_children)}")
    
    # 按位置匹配子市场
    for i, (op_child, pm_child) in enumerate(zip(opinion_children, polymarket_children)):
        # 拼接标题
        combined_title = f"{parent_title} - {op_child['child_title']}"
        
        match = {
            "question": combined_title,
            "opinion_market_id": op_child['market_id'],
            "opinion_yes_token": op_child['yes_token_id'],
            "opinion_no_token": op_child['no_token_id'],
            "polymarket_condition_id": pm_child['condition_id'],
            "polymarket_yes_token": pm_child['yes_token_id'],
            "polymarket_no_token": pm_child['no_token_id'],
            "polymarket_slug": pm_child['slug'],
            "similarity_score": 1.0
        }
        new_matches.append(match)
        print(f"✓ 匹配: {combined_title}")

# 合并到现有的匹配结果
all_matches = existing_matches + new_matches

# 保存到 market_matches_unmatched.json
with open('market_matches_unmatched.json', 'w', encoding='utf-8') as f:
    json.dump(all_matches, f, indent=2, ensure_ascii=False)

print(f"\n✅ 转换完成!")
print(f"   原有匹配: {len(existing_matches)} 个")
print(f"   新增匹配: {len(new_matches)} 个")
print(f"   总计: {len(all_matches)} 个")
print(f"\n已保存到: market_matches_unmatched.json")
