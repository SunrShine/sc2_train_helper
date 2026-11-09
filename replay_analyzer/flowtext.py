"""从「运营：」总结卡的文字串解析出结构化步骤。

UP 主在每期视频末尾会放一张总结卡，用一行 token 写完整建造顺序，例如：

    BS兵营气矿气矿VF死神星轨BS VF双倍 火车2死神BS火车VS2死神火车双科技

本模块把这串文字变成 step 列表（动作 / 标签 / 顺序号），供 reports 使用。
规则写死在这里，改词表只改 TOKENS。
"""

import re

# (正则, 动作名, 标签)  —— 长的放前面，避免「2死神」被「死神」先吃掉
TOKENS = (
    (r"兴奋剂", "兴奋剂（研究）", "tech:stim"),
    (r"双科技", "挂双科技挂件", "build:techlab×2"),
    (r"科技挂件", "挂科技挂件", "build:techlab"),
    (r"科技", "挂科技挂件", "build:techlab"),
    (r"双倍", "挂双倍（反应堆）", "build:reactor"),
    (r"补给站|BS", "补给站", "build:supply_depot"),
    (r"兵营", "兵营", "build:barracks"),
    (r"气矿", "气矿", "gas"),
    (r"VF", "重工", "build:factory"),
    (r"VS", "星港", "build:starport"),
    (r"星轨", "变形星轨", "build:orbital"),
    (r"二基地|2基地|三基地|3基地|基地", "开矿", "expand"),
    (r"地堡", "地堡", "build:bunker"),
    (r"死神", "死神", "unit:reaper"),
    (r"火车", "火车", "unit:hellion"),
    (r"光头", "光头", "unit:marauder"),
    (r"枪兵", "枪兵", "unit:marine"),
    (r"导弹车", "导弹车", "unit:cyclone"),
    (r"女妖", "女妖", "unit:banshee"),
    (r"渡鸦", "渡鸦", "unit:raven"),
    (r"维京", "维京", "unit:viking"),
    (r"坦克", "坦克", "unit:siege_tank"),
    (r"运输机|船兵|运输|装船|运", "运输机", "unit:medivac"),
    (r"雷", "寡妇雷", "unit:widow_mine"),
    (r"解放", "解放者", "unit:liberator"),
    (r"大和", "大和", "unit:battlecruiser"),
)

COUNT = re.compile(r"^([0-9一二三四五六七八九十两]+)\s*")


def _count(text):
    match = COUNT.match(text)
    if not match:
        return 1, text
    raw = match.group(1)
    mapping = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    value = int(raw) if raw.isdigit() else mapping.get(raw, 1)
    return value, text[match.end():]


def parse_sequence(text, start_number=1):
    """把「BS兵营气矿气矿VF…」解析成 steps。"""
    if not text:
        return []
    steps = []
    number = start_number
    gas_index = 0
    cursor = text
    while cursor:
        cursor = cursor.lstrip(" ，,、/|")
        if not cursor:
            break
        count, rest = _count(cursor)
        matched = False
        for pattern, action, tag in TOKENS:
            found = re.match(pattern, rest)
            if not found:
                continue
            label = action
            tags = [tag]
            if tag == "gas":
                gas_index += 1
                tags = ["gas:%d" % gas_index]
                label = "气矿（第 %d 个）" % gas_index
            if count > 1:
                label = "%s ×%d" % (label, count)
                tags = [t + "×%d" % count for t in tags]
            steps.append({"n": number, "action": label, "tags": tags,
                          "clock": None, "source": "frame", "confidence": "high"})
            number += 1
            cursor = rest[found.end():]
            matched = True
            break
        if not matched:
            # 不认识的字符，跳过一格继续（避免死循环）
            cursor = rest[1:]
    return steps


if __name__ == "__main__":
    demo = "BS兵营气矿气矿VF死神星轨BS VF双倍 火车2死神BS火车VS2死神火车双科技"
    for step in parse_sequence(demo):
        print(step["n"], step["action"], step["tags"])
