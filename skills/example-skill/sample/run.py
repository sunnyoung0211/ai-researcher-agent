"""验证用的样例运行：air skills verify 会调用 run(skill, input_dir, out_dir)。

真实的 skill 一般要调模型；验证时用确定性的写法（或固定的模型输出）产生结果，这样每次结果都一样，
可以和 sample/expected/ 逐字比对。返回值（可选）会交给检查函数；不返回时检查 out_dir 里的每个文件。
"""


def run(skill, input_dir, out_dir):
    text = (input_dir / "notes.txt").read_text(encoding="utf-8")
    points = [p.split("。")[0] + "。" for p in text.split("\n\n") if p.strip()]
    summary = "# 摘要\n\n" + "".join(f"- {p}\n" for p in points)
    (out_dir / "summary.md").write_text(summary, encoding="utf-8")
    return summary
