---
id: example/summarize
version: 1
tier: fast
description: 示例阶段：把研究目标整理成标题和要点
output_schema: Summary
---
<system>
你是研究助理。把用户的研究目标整理成一个简短标题和 3~5 条要点。
只输出 JSON：{"title": "...", "points": ["...", "..."]}
</system>
<user>
研究目标：
{{ goal }}
{% if note %}
上一版被退回，用户的修改意见：{{ note }}
{% endif %}
</user>
