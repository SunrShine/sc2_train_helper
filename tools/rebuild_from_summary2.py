import json
import sys

sys.path.insert(0, ".")
from replay_analyzer.flowtext import parse_sequence

PATH = "reports/tutorial-steps-pilot.json"

# 补完剩下 9 条：以「运营：」总结卡为准
DATA = [
    # 5) 野 2 兵营：这期不是建造串，是"野2兵营 vs 各种开局"的应对手册
    (5, "BV1LT4y1X7E6", "野 2 兵营（含各种应对）",
     "兵营气矿开 气矿兵营开",
     ["野3兵营打野2兵营", "野2兵营打野2兵营", "野2兵营打家里2兵营",
      "野2兵营打野光头", "野2兵营打野3BB机枪", "野2兵营打家里1兵营外面1兵营"],
     {}, "这期的总结卡是「应对矩阵」而不是建造串：左边列我方野2兵营，右边列对手各种开局怎么打。场次 24 分钟，含完整对局"),

    (24, "BV1mr4y1z79t", "双气 2 死神 1 火车 1 雷 → 导弹车 → 2 渡鸦（对局讲解）",
     "BS兵营气矿气矿VF 2基地 2死神1火车1雷 后续挂科技挂件1个导弹车2个渡鸦",
     ["2 死神 1 火车开偏防守，可以防住双气 3 死神 2 火车开",
      "也可以转一船兵骚扰（第一时间运输机 4 枪 1 坦）",
      "出导弹车可以防女妖、空投、1 船兵",
      "此开局可攻可守，为近期职业选手常用"],
     {}, "总结卡与 #25 内容几乎相同（作者连续两期讲同一族打法）"),

    (25, "BV1Fi4y1171Y", "双气 2 死神 1 火车 1 雷（TVT 职业常用运营开局）",
     "BS兵营气矿气矿BS VF 2基地 2死神1火车1雷 后续挂科技挂件1个导弹车2个渡鸦",
     ["2 死神 1 火车开偏防守，可以防住双气 3 死神 2 火车开",
      "也可以转一船兵骚扰（第一时间运输机 4 枪 1 坦）",
      "出导弹车可以防女妖、空投、1 船兵",
      "此开局可攻可守，为近期职业选手常用"],
     {}, "总结卡与 #24 内容几乎相同（连续两期同一族打法）"),

    (35, "BV1aL4y1T75T", "兵营气矿开：死神 枪兵 地堡 火车 导弹车 运输机",
     "BS兵营气矿死神星轨2基地补给站枪兵VF地堡枪兵气矿双倍星轨火车VS气矿科技运输机 导弹车",
     ["兵营气矿开野兵营防法参考前面几期",
      "经济更好，比一般双气开多 3 个农民",
      "1 船兵骚扰 TIMING 在 4:30~4:40"],
     {}, "兵营气矿开（单气）路线，比双气开多 3 农民"),

    (39, "BV1CF41147gq", "双气 2 死神 1 火车 1 雷 1 运输机（2-1-1 迭代）",
     "",
     ["本期是 2-1-1 套的迭代变化（ByuN）"],
     {}, "这期总结卡文字与 #41 相同（作者复用同一张卡），归属存疑；步骤暂保留画面编号抓到的 4 条"),

    (41, "BV1D3411Wju", "双气 6 死 2 枪 光头空投",
     "BS兵营气矿气矿死神星轨补给站VF VS BS",
     ["运营细节：先下 VS 再下 BS，这样运输机会更快",
      "4:10 一船兵到对手主矿视野盲区",
      "目标：给对手压力、杀农民、换到足够战损",
      "如果侦查到 5 死神 3 火车，可以换个开局（比如双气 3 死神 1 火车 1 雷）"],
     {}, "光头空投；4:10 是作者给的硬时间点"),

    (37, "BV11Z4y1r7aR", "双气 6 火车 渡鸦",
     "BS兵营气矿气矿VF死神星轨BS VF双倍 火车BS VS2死神火车2死神",
     ["（防御向）气矿开野兵营防法：第一时间侦查到不出死神",
      "枪兵火车不停（必要时拖 4~5 个农民配合）",
      "探路农民野 VS（非常近）甚至野在对手二矿",
      "VS 出解放/维京，维京落地打导弹车；3 火车维京强吃导弹车（CURE 防法）"],
     {}, "本期前半是野兵营防法，后半才是 6 火车渡鸦运营"),
]

# 6 / 7 是 2022 年的老格式（片尾是动漫 ED，没有总结卡），保留画面编号抓到的步骤
OLD_FORMAT = {
    6: ("野单兵营光头（野兵营 → 光头骚扰 → 隐形女妖）",
        "2022 年老格式：没有总结卡，片尾是动漫 ED；步骤来自画面上的「第 N 步」标注"),
    7: ("野 2VS 大和（野两星港出大和）",
        "2022 年老格式：只有 1、2 两步编号，其余是防守思路讲解"),
}


def main():
    data = json.load(open(PATH, encoding="utf-8"))
    by_number = {v["number"]: v for v in data["videos"]}

    for number, bvid, build, sequence, plan, clocks, note in DATA:
        steps = parse_sequence(sequence) if sequence else list(by_number.get(number, {}).get("steps") or [])
        for index, clock in clocks.items():
            if 0 < index <= len(steps):
                steps[index - 1]["clock"] = clock
        entry = by_number.get(number, {"number": number, "bvid": bvid, "matchup": "TvT"})
        entry.update({
            "number": number, "bvid": bvid, "matchup": "TvT",
            "status": "complete" if sequence else "partial",
            "build": build,
            "sequence_text": sequence,
            "steps": steps,
            "plan": plan,
            "note": note,
        })
        by_number[number] = entry

    for number, (build, note) in OLD_FORMAT.items():
        entry = by_number.get(number)
        if entry:
            entry.update({"build": build, "status": "partial", "note": note,
                          "plan": entry.get("plan") or []})
            by_number[number] = entry

    data["videos"] = sorted(by_number.values(), key=lambda v: v["number"])
    json.dump(data, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    total = sum(len(v.get("steps") or []) for v in data["videos"])
    print("视频 %d 条，步骤 %d" % (len(data["videos"]), total))
    for v in data["videos"]:
        print("  #%-3s %-9s %2d 步  %s" % (v["number"], v.get("status"),
                                           len(v.get("steps") or []), v.get("build", "")[:38]))


if __name__ == "__main__":
    main()
