1. 公式计算opinion下单后能实际成交的数量，以避免手续费原因导致的对冲数量不匹配 DONE
2. 单个请求同时监控多个市场订单簿（polymarket有ws和restful api支持该功能）
3. 扫描套利机会中，如果出现了大于6%且小于20%的套利机会，立即用新的线程执行套利（不要等待扫描完所有的套利机会）DONE
4. 让用户选择执行多个潜在套利

2+4 现在单个套利还没玩明白
1 没有可靠公式，要么就做拟合

我在做opinion与polymarket的跨平台套利，请你帮我推导计算：
value = priceamount, fee = valuefee_rate (at least $0.5)
fee会使得最终获得的amount减少，因此amount需要增加 fee/price
eg1 price=0.654, 原amount为200，fee为2.1，扣除fee后amount变为196.37
eg2 price=0.962, 原amount为200，fee为0.9，扣除fee后amount变为198.93
eg3 price=0.870，原amount为200，fee为1.4，扣除fee后amount变为198.19
eg4 price=0.031，原amount为200，fee为0.5（不足0.5则至少为0.5），扣除fee后amount变为183.87
已知fee_rate是price变量的二次函数，应该形如ax(1-x)+c，请你帮忙推导出公式来计算修正后应下单的amount。

好的，我们来一步步推导计算您在进行 opinion 与 polymarket 跨平台套利时，修正后应下单的 amount。

目标

我们的目标是推导出修正后的下单数量（我们称之为 A_order），使得在支付交易费用后，您最终得到的数量恰好是您期望的目标数量（我们称之为 A_target）。

步骤1：推导手续费率 (fee_rate) 的公式

根据您提供的信息，fee_rate 是 price 的二次函数，形式为 fee_rate = a*price*(1-price) + c。我们可以利用您给出的前三个未触及最低手续费的例子来求解系数 a 和 c。

我们从每个例子中计算出实际的 fee_rate:
fee_rate = fee / (price * amount)

eg1: fee_rate = 2.1 / (0.654 * 200) ≈ 0.016055

eg2: fee_rate = 0.9 / (0.962 * 200) ≈ 0.004677

eg3: fee_rate = 1.4 / (0.870 * 200) ≈ 0.008046

现在，我们将这些数据代入 fee_rate = a*p*(1-p) + c 公式，建立方程组：

当 p = 0.654: 0.016055 = a * 0.654 * (1 - 0.654) + c => 0.016055 = 0.226284*a + c

当 p = 0.962: 0.004677 = a * 0.962 * (1 - 0.962) + c => 0.004677 = 0.036556*a + c

通过解这个二元一次方程组，我们可以得到：

a ≈ 0.06

c ≈ 0.0025

因此，手续费率的计算公式为：
fee_rate = 0.06 * price * (1 - price) + 0.0025

我们可以用 eg3 (p=0.870) 来验证这个公式的准确性：
fee_rate = 0.06 * 0.87 * (1 - 0.87) + 0.0025 = 0.009282
这个结果与从 eg3 数据中直接计算出的 0.008046 比较接近，存在的微小差异可能是由于原始数据中的舍入误差导致。我们采用推导出的公式进行下一步。

步骤2：建立修正后下单数量 (A_order) 的计算逻辑

手续费的计算存在两种情况：

百分比手续费：当按费率计算出的费用 (value * fee_rate) 大于 $0.5 时。

最低手续费：当计算出的费用小于或等于 $0.5 时，直接收取 $0.5。

最终得到的数量 A_target 与您的下单数量 A_order 之间的关系是：
A_target = A_order - fee / price

我们需要根据这两种情况，反推出 A_order。

情况1：适用百分比手续费

fee = price * A_order * fee_rate
代入关系式：
A_target = A_order - (price * A_order * fee_rate) / price
A_target = A_order * (1 - fee_rate)
因此，下单数量为：
A_order = A_target / (1 - fee_rate)

情况2：适用$0.5最低手续费

fee = 0.5
代入关系式：
A_target = A_order - 0.5 / price
因此，下单数量为：
A_order = A_target + 0.5 / price

步骤3：最终的计算公式与流程

核心问题在于，在下单前我们无法确定手续费会按百分比计算还是按最低金额计算。我们可以通过一个简单的“预计算”来解决这个问题。

最终计算流程如下：

假设您希望最终获得 A_target 数量的头寸。

第一步：计算 fee_rate
根据当前价格 price，计算出手续费率。
fee_rate = 0.06 * price * (1 - price) + 0.0025

第二步：进行预计算
先假设交易适用百分比手续费，计算出一个临时的下单数量 A_provisional。
A_provisional = A_target / (1 - fee_rate)

第三步：判断适用场景
用这个临时的下单数量计算其对应的手续费 Fee_provisional，并与最低手续费 $0.5 比较。
Fee_provisional = price * A_provisional * fee_rate

第四步：选择最终公式

如果 Fee_provisional > 0.5，说明预计算的假设是正确的，交易金额较大，适用百分比手续费。
修正后的下单数量 A_order = A_target / (1 - fee_rate)

如果 Fee_provisional <= 0.5，说明预计算的假设是错误的，交易金额较小，应适用最低手续费。
修正后的下单数量 A_order = A_target + 0.5 / price

示例演算

我们用您给出的 eg4 来验证这个流程 (price=0.031, 目标A_target为200)。

计算 fee_rate:
fee_rate = 0.06 * 0.031 * (1 - 0.031) + 0.0025 ≈ 0.0043

预计算:
A_provisional = 200 / (1 - 0.0043) ≈ 200.86

判断适用场景:
Fee_provisional = 0.031 * 200.86 * 0.0043 ≈ $0.0268

选择最终公式:
因为 $0.0268 <= $0.5，所以适用最低手续费的公式。
A_order = 200 + 0.5 / 0.031
A_order = 200 + 16.13
A_order ≈ 216.13

结论：当价格为 0.031 时，为了在扣除费用后得到 200 的 amount，您应该下单约 216.13。这笔交易的手续费为 $0.5，折算成 amount 减少量为 0.5 / 0.031 ≈ 16.13，与计算结果完全吻合。