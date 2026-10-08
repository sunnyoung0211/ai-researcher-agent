"""检查函数：输入是 skill 的输出，返回问题列表（字符串或 ValidationIssue），没有问题返回空列表。"""


def check_not_empty(output):
    return [] if str(output).strip() else ["输出为空"]


def check_has_heading(output):
    return [] if str(output).lstrip().startswith("# ") else ["第一行必须是一级标题"]
