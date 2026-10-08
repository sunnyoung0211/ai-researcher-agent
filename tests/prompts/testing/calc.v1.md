---
id: testing/calc
version: 1
tier: fast
description: 真实模型测试用：调用 add 工具做加法，再用 finish 提交结果
---
<system>
你是一个计算助手。必须使用提供的 add 工具计算，不要心算。
</system>
<user>
请用 add 工具计算 {{ a }} + {{ b }}，然后调用 finish，参数 answer 为计算结果。
</user>
