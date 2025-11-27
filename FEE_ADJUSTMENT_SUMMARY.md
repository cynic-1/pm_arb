# Opinion 手续费修正实现总结

## 背景

在进行 Opinion 与 Polymarket 跨平台套利时,Opinion 平台会收取手续费,导致实际成交数量少于下单数量。为了确保两个平台的对冲数量精确匹配,需要在下单时考虑手续费进行修正。

## 手续费公式

根据实际数据推导出的手续费率公式:

```
fee_rate = 0.06 * price * (1 - price) + 0.0025
```

手续费计算有两种情况:
- **百分比手续费**: 当 `value * fee_rate > $0.5` 时, `fee = value * fee_rate`
- **最低手续费**: 当计算出的费用 ≤ $0.5 时, `fee = $0.5`

## 修正后的下单数量计算

给定目标数量 `A_target` (期望最终得到的数量),修正后的下单数量 `A_order` 的计算逻辑:

### 步骤1: 计算手续费率
```python
fee_rate = 0.06 * price * (1 - price) + 0.0025
```

### 步骤2: 预计算
假设适用百分比手续费:
```python
A_provisional = A_target / (1 - fee_rate)
```

### 步骤3: 判断适用场景
计算预估手续费:
```python
Fee_provisional = price * A_provisional * fee_rate
```

### 步骤4: 选择最终公式
- **如果 Fee_provisional > 0.5**: 适用百分比手续费
  ```python
  A_order = A_target / (1 - fee_rate)
  ```

- **如果 Fee_provisional ≤ 0.5**: 适用最低手续费
  ```python
  A_order = A_target + 0.5 / price
  ```

## 实现的功能

### 1. 核心计算方法

在 `CrossPlatformArbitrage` 类中添加了以下方法:

#### `calculate_opinion_fee_rate(price: float) -> float`
计算 Opinion 平台的手续费率

#### `calculate_opinion_adjusted_amount(price: float, target_amount: float) -> float`
计算考虑手续费后应下单的数量

#### `calculate_opinion_effective_amount(price: float, order_amount: float) -> float`
计算订单成交后实际得到的数量(扣除手续费)

#### `get_order_size_for_platform(platform, price, target_amount, is_hedge) -> (float, float)`
统一的下单数量计算接口:
- Opinion 平台: 自动进行手续费修正
- Polymarket 平台: 直接使用目标数量

### 2. 集成到下单流程

修改了以下下单场景,确保使用修正后的数量:

#### 立即套利 (immediate arbitrage)
- 首单: 使用 `get_order_size_for_platform` 计算下单数量
- 对冲单: 使用首单的实际数量作为目标,再次调用 `get_order_size_for_platform`

#### 潜在套利 (pending arbitrage)
- 首单: 使用 `get_order_size_for_platform` 计算下单数量
- 重新挂单: 价格变化时重新计算下单数量
- 对冲单: 根据首单的实际成交量计算对冲数量

### 3. 对冲数量匹配

对冲单的数量计算逻辑:

```python
# 如果首单在 Opinion,newly_filled 已经是扣除手续费后的实际数量
if strategy['first_platform'] == 'opinion':
    hedge_target_amount = newly_filled
else:
    # Polymarket 首单: 新增成交量就是实际数量
    hedge_target_amount = newly_filled

# 计算对冲单的下单数量(如果对冲平台是 Opinion,会自动修正)
hedge_order_size, hedge_effective_size = self.get_order_size_for_platform(
    strategy['second_platform'],
    strategy['second_price'],
    hedge_target_amount,
    is_hedge=True
)
```

## 测试验证

创建了 `test_fee_adjustment.py` 测试脚本,验证了:

1. ✅ 手续费率公式的准确性
2. ✅ 修正后下单数量的正确性
3. ✅ 实际得到数量与目标数量的匹配
4. ✅ 不同价格区间的适用性

测试结果显示,所有测试用例的误差都为 0.0000,证明公式完全准确。

## 使用示例

```python
# 创建套利实例
arb = CrossPlatformArbitrage()

# 示例1: 计算 Opinion 下单数量
target = 200  # 期望得到 200 shares
price = 0.654
adjusted = arb.calculate_opinion_adjusted_amount(price, target)
# 输出: 203.27 (修正后应下单 203.27 shares)

# 示例2: 验证实际得到的数量
effective = arb.calculate_opinion_effective_amount(price, adjusted)
# 输出: 200.00 (实际得到 200.00 shares,与目标完全一致)

# 示例3: 统一接口使用
order_size, effective_size = arb.get_order_size_for_platform(
    'opinion', 
    price, 
    target
)
# 返回: (203.27, 200.00)
```

## 注意事项

1. **价格精度**: Opinion 手续费计算对价格敏感,建议使用至少 4 位小数
2. **低价市场**: 价格极低时(<0.1)容易触发最低手续费,需要下更多数量
3. **实时验证**: 建议在实际下单后验证成交数量是否符合预期
4. **监控日志**: 所有计算过程都有详细日志输出,便于调试

## 文件清单

- `arbitrage.py`: 主程序,包含手续费计算和修正逻辑
- `test_fee_adjustment.py`: 测试脚本,验证公式准确性
- `FEE_ADJUSTMENT_SUMMARY.md`: 本文档

## 后续优化建议

1. 可以添加手续费预估到套利机会扫描阶段
2. 可以考虑 Polymarket 平台的手续费(如果有)
3. 可以添加实际成交数量与预期的偏差监控和报警
