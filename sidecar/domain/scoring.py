"""
评分模型。

后续职责：
1. 计算 s_global（基于 initScore/penalty/lastTime）。
2. 计算 s_local（alpha/beta 衰减与增益）。
3. 计算 w_confidence 与 s_final。
4. 保持纯函数，便于单元测试和参数回放。
"""

