#!/usr/bin/env python3
"""
测试 Opinion 手续费计算公式
验证修正后的下单数量是否能达到目标数量
"""


def calculate_opinion_fee_rate(price: float) -> float:
    """
    计算 Opinion 平台的手续费率
    
    根据推导公式: fee_rate = 0.06 * price * (1 - price) + 0.0025
    """
    return 0.06 * price * (1 - price) + 0.0025


def calculate_opinion_adjusted_amount(price: float, target_amount: float) -> float:
    """
    计算 Opinion 平台考虑手续费后应下单的数量
    
    目标: 使得扣除手续费后,实际得到的数量等于 target_amount
    """
    # 步骤1: 计算手续费率
    fee_rate = calculate_opinion_fee_rate(price)
    
    # 步骤2: 预计算 (假设适用百分比手续费)
    A_provisional = target_amount / (1 - fee_rate)
    
    # 步骤3: 计算预估手续费
    Fee_provisional = price * A_provisional * fee_rate
    
    # 步骤4: 判断适用场景并返回最终数量
    if Fee_provisional > 0.5:
        # 适用百分比手续费
        A_order = target_amount / (1 - fee_rate)
        print(f"💰 Opinion 手续费计算: price={price:.4f}, fee_rate={fee_rate:.6f}, "
              f"预估手续费=${Fee_provisional:.4f} (百分比手续费)")
    else:
        # 适用最低手续费 $0.5
        A_order = target_amount + 0.5 / price
        print(f"💰 Opinion 手续费计算: price={price:.4f}, fee_rate={fee_rate:.6f}, "
              f"预估手续费=${Fee_provisional:.4f} -> 最低手续费 $0.5")
    
    print(f"   目标数量: {target_amount:.2f} -> 修正后下单数量: {A_order:.2f}")
    return A_order


def calculate_opinion_effective_amount(price: float, order_amount: float) -> float:
    """
    计算 Opinion 订单成交后实际得到的数量 (扣除手续费)
    
    关系: effective_amount = order_amount - fee / price
    """
    # 计算手续费率
    fee_rate = calculate_opinion_fee_rate(price)
    
    # 计算订单价值
    value = price * order_amount
    
    # 计算手续费 (至少 $0.5)
    fee = max(value * fee_rate, 0.5)
    
    # 计算实际得到的数量
    effective_amount = order_amount - fee / price
    
    print(f"💰 Opinion 实际数量计算: 订单数量={order_amount:.2f}, "
          f"手续费=${fee:.4f}, 实际数量={effective_amount:.2f}")
    
    return effective_amount


def test_fee_calculation():
    """测试手续费计算公式"""
    print("="*80)
    print("测试 Opinion 手续费计算公式")
    print("="*80)
    print()
    
    # 测试用例 (来自 todo.md)
    test_cases = [
        {
            "name": "eg1",
            "price": 0.654,
            "target_amount": 200,
            "expected_fee": 2.1,
            "expected_effective": 196.37,
        },
        {
            "name": "eg2",
            "price": 0.962,
            "target_amount": 200,
            "expected_fee": 0.9,
            "expected_effective": 198.93,
        },
        {
            "name": "eg3",
            "price": 0.870,
            "target_amount": 200,
            "expected_fee": 1.4,
            "expected_effective": 198.19,
        },
        {
            "name": "eg4 (最低手续费)",
            "price": 0.031,
            "target_amount": 200,
            "expected_fee": 0.5,
            "expected_effective": 183.87,
        },
    ]
    
    print("步骤 1: 测试手续费率计算\n")
    for case in test_cases:
        price = case["price"]
        expected_fee = case["expected_fee"]
        target_amount = case["target_amount"]
        
        # 计算手续费率
        fee_rate = calculate_opinion_fee_rate(price)
        
        # 根据原始数据计算实际的手续费率
        actual_fee_rate = expected_fee / (price * target_amount)
        
        print(f"{case['name']}: price={price:.3f}")
        print(f"  公式计算的 fee_rate = {fee_rate:.6f}")
        print(f"  实际的 fee_rate = {actual_fee_rate:.6f}")
        print(f"  误差 = {abs(fee_rate - actual_fee_rate):.6f}")
        print()
    
    print("="*80)
    print("\n步骤 2: 测试修正后的下单数量计算\n")
    
    for case in test_cases:
        price = case["price"]
        target_amount = case["target_amount"]
        expected_effective = case["expected_effective"]
        
        print(f"\n{case['name']}: price={price:.3f}, 目标数量={target_amount}")
        print("-"*80)
        
        # 计算修正后的下单数量
        adjusted_amount = calculate_opinion_adjusted_amount(price, target_amount)
        
        # 验证: 用修正后的数量计算实际得到的数量
        effective_amount = calculate_opinion_effective_amount(price, adjusted_amount)
        
        print(f"\n结果验证:")
        print(f"  目标数量: {target_amount:.2f}")
        print(f"  修正后下单数量: {adjusted_amount:.2f}")
        print(f"  实际得到数量: {effective_amount:.2f}")
        print(f"  预期实际数量: {expected_effective:.2f}")
        print(f"  误差: {abs(effective_amount - target_amount):.4f}")
        
        # 检查是否达到目标
        tolerance = 0.5  # 允许 0.5 的误差
        if abs(effective_amount - target_amount) < tolerance:
            print(f"  ✅ 通过: 实际数量接近目标数量")
        else:
            print(f"  ⚠️  警告: 实际数量与目标数量偏差较大")
        
        print()
    
    print("="*80)
    print("\n步骤 3: 额外测试 - 不同价格区间\n")
    
    test_prices = [0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99]
    target = 200
    
    print(f"目标数量: {target}")
    print(f"\n{'价格':<8} {'手续费率':<12} {'修正数量':<12} {'实际数量':<12} {'误差':<8}")
    print("-"*80)
    
    for price in test_prices:
        fee_rate = calculate_opinion_fee_rate(price)
        adjusted = calculate_opinion_adjusted_amount(price, target)
        effective = calculate_opinion_effective_amount(price, adjusted)
        error = abs(effective - target)
        
        print(f"{price:<8.3f} {fee_rate:<12.6f} {adjusted:<12.2f} {effective:<12.2f} {error:<8.4f}")
    
    print("\n" + "="*80)
    print("测试完成!")
    print("="*80)


if __name__ == "__main__":
    test_fee_calculation()
